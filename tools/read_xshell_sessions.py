import os, sys
sys.stdout.reconfigure(encoding='utf-8')

session_dir = r'C:\Users\admin\Documents\NetSarang Computer\8\Xshell\Sessions'

for root, dirs, files in os.walk(session_dir):
    dirs.sort()
    files.sort()
    for f in files:
        if f.endswith('.xsh'):
            fp = os.path.join(root, f)
            with open(fp, 'rb') as fh:
                raw = fh.read()
            text = raw.decode('utf-16-le', errors='replace')
            sections = []
            current = {}
            for line in text.splitlines():
                line = line.strip()
                if line.startswith('[') and line.endswith(']'):
                    if current:
                        sections.append(current)
                    current = {'__section__': line}
                elif '=' in line and current:
                    k, _, v = line.partition('=')
                    current[k.strip()] = v.strip()
            if current:
                sections.append(current)

            conn = next((s for s in sections if s.get('__section__') == '[CONNECTION]'), {})
            auth = next((s for s in sections if s.get('__section__') == '[CONNECTION:AUTHENTICATION]'), {})

            rel = os.path.relpath(fp, session_dir)
            group = os.path.dirname(rel)
            name = os.path.splitext(os.path.basename(rel))[0]

            print(f'File: {rel}')
            print(f'  Group: {group}')
            print(f'  Name: {name}')
            print(f'  Host: {conn.get("Host", "?")}')
            print(f'  Port: {conn.get("Port", "22")}')
            print(f'  Protocol: {conn.get("Protocol", "SSH")}')
            print(f'  UserName: {auth.get("UserName", "root")}')
            print(f'  UseKeyFile: {auth.get("UseKeyFile", "0")}')
            print(f'  KeyFilePath: {auth.get("KeyFilePath", "")}')
            print(f'  HasPassword: {bool(auth.get("Password"))}')
            print(f'  HasEncryptPassword: {bool(auth.get("EncryptPassword"))}')
            print()
