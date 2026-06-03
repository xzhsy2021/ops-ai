"""命令安全验证服务 - 注入防护与危险命令拦截"""
import re
import logging
from typing import Tuple, Optional, List

logger = logging.getLogger(__name__)

_BLOCKED_PATTERNS = [
    (re.compile(r':\(\)\{\s*:\|\:&\s*\};', re.IGNORECASE), "fork bomb"),
    (re.compile(r'\brm\s+(-\S*\s+)*--no-preserve-root\b', re.IGNORECASE), "destructive rm --no-preserve-root"),
    (re.compile(r'\brm\s+-\S*r\S*f\S*\s+/\s*$', re.IGNORECASE), "destructive rm -rf /"),
    (re.compile(r'\bmkfs\b', re.IGNORECASE), "filesystem format"),
    (re.compile(r'\bdd\s+if=.*of=/dev/', re.IGNORECASE), "raw device write"),
    (re.compile(r'\b(shutdown|reboot|poweroff|halt)\b', re.IGNORECASE), "system shutdown/reboot"),
    (re.compile(r'>\s*/dev/sd[a-z]', re.IGNORECASE), "raw disk write"),
    (re.compile(r'\bchmod\s+(-R\s+)?0{3,4}\s+/$', re.IGNORECASE), "recursive zero permissions on root"),
]

_DANGEROUS_PATTERNS = [
    (re.compile(r'\brm\s+(-\S*r\S*)\s+', re.IGNORECASE), "recursive delete"),
    (re.compile(r'\bkill\s+-9\s+1\b', re.IGNORECASE), "kill init"),
    (re.compile(r'\biptables\s+-F\b', re.IGNORECASE), "flush firewall rules"),
    (re.compile(r'\bsystemctl\s+(stop|disable)\s+(ssh|sshd|nginx|apache2|httpd)\b', re.IGNORECASE), "stop critical service"),
    (re.compile(r'\bcrontab\s+-r\b', re.IGNORECASE), "remove crontab"),
    (re.compile(r'\bmv\s+.*\s+/(dev|null)\b', re.IGNORECASE), "move to /dev/null"),
]

_SHELL_INJECTION_PATTERNS = [
    (re.compile(r'\$\(\s*rm\b', re.IGNORECASE), "command substitution with rm"),
    (re.compile(r'`[^`]*\brm\b', re.IGNORECASE), "backtick substitution with rm"),
    (re.compile(r'\b(wget|curl)\s+.*\|\s*(ba)?sh\b', re.IGNORECASE), "pipe remote content to shell"),
]


def validate_command(command: str) -> Tuple[bool, Optional[str], str]:
    if not command or not command.strip():
        return False, "command is empty", "empty"

    for pattern, desc in _SHELL_INJECTION_PATTERNS:
        if pattern.search(command):
            logger.warning(f"Blocked injection attempt: {desc} pattern matched in: {command[:100]}")
            return False, f"blocked injection: {desc}", "blocked"

    for pattern, desc in _BLOCKED_PATTERNS:
        if pattern.search(command):
            logger.warning(f"Blocked command attempt: {desc} pattern matched in: {command[:100]}")
            return False, f"blocked: {desc}", "blocked"

    for pattern, desc in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            logger.info(f"Dangerous command detected: {desc} in: {command[:100]}")
            return True, f"dangerous: {desc}", "dangerous"

    return True, None, "safe"


def is_dangerous_command(command: str) -> bool:
    _, _, level = validate_command(command)
    return level in ("dangerous", "blocked")


def sanitize_command_output(output: str, max_length: int = 50000) -> str:
    if not output:
        return output
    sanitized = re.sub(r'(password|passwd|secret|token|key|credential)\s*[=:]\s*\S+',
                       r'\1=***REDACTED***', output, flags=re.IGNORECASE)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "\n... [truncated]"
    return sanitized


def validate_file_path(path: str) -> Tuple[bool, Optional[str]]:
    if not path:
        return False, "path is empty"
    if '..' in path.split('/'):
        return False, "path traversal detected"
    if '\x00' in path:
        return False, "null byte in path"
    return True, None


class TerminalInputGuard:
    """Stateful backend guard for interactive terminal input.

    The frontend already warns before sending risky commands, but WebSocket
    input must be treated as untrusted. This guard buffers the current terminal
    line and validates it when Enter is pressed. It can also safely handle
    pasted multi-line text: only validated lines are forwarded to the SSH
    channel, while blocked lines are cleared before execution.
    """

    def __init__(self, *, block_dangerous: bool = True, max_line_length: int = 1000):
        self.block_dangerous = block_dangerous
        self.max_line_length = max_line_length
        self._line = ""

    @property
    def pending_line(self) -> str:
        return self._line

    def reset(self) -> None:
        self._line = ""

    def feed(self, data: bytes | str) -> tuple[bytes, List[dict]]:
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="ignore")
        else:
            text = str(data or "")

        out = bytearray()
        events: List[dict] = []

        for ch in text:
            # Backspace / DEL: keep remote terminal and our local line buffer in sync.
            if ch in ("\x7f", "\b"):
                self._line = self._line[:-1]
                out.extend(ch.encode("utf-8", errors="ignore"))
                continue

            # Ctrl+C should cancel the current line. Ctrl+U clears line.
            if ch in ("\x03", "\x15"):
                self._line = ""
                out.extend(ch.encode("utf-8", errors="ignore"))
                continue

            # Escape sequences are navigation/editing controls; forward them but
            # do not attempt to interpret them as command text.
            if ch == "\x1b":
                out.extend(ch.encode("utf-8", errors="ignore"))
                continue

            if ch in ("\r", "\n"):
                command = self._line.strip()
                if not command:
                    self._line = ""
                    out.extend(ch.encode("utf-8", errors="ignore"))
                    continue

                allowed, reason, level = validate_command(command)
                should_block = (not allowed) or (level == "dangerous" and self.block_dangerous)
                if should_block:
                    reason_text = reason or level or "unknown risk"
                    events.append({"command": command, "reason": reason_text, "level": level})
                    # Ctrl+U clears a line that may already have been typed into
                    # the remote pty, then print an operator-visible warning.
                    warning = f"\x15\r\n[OPS] blocked terminal command ({level}): {reason_text}\r\n"
                    out.extend(warning.encode("utf-8", errors="replace"))
                    self._line = ""
                    continue

                self._line = ""
                out.extend(ch.encode("utf-8", errors="ignore"))
                continue

            # Printable text and tabs contribute to the command line.
            if ch == "\t" or ch >= " ":
                if len(self._line) < self.max_line_length:
                    self._line += ch
                    out.extend(ch.encode("utf-8", errors="ignore"))
                else:
                    events.append({"command": self._line[:120], "reason": "line too long", "level": "blocked"})
                    out.extend(b"\x15\r\n[OPS] blocked terminal command: line too long\r\n")
                    self._line = ""
                continue

            # Other control characters are forwarded without changing the line buffer.
            out.extend(ch.encode("utf-8", errors="ignore"))

        return bytes(out), events
