import type { ReactNode } from 'react'
import { CircleCheck } from 'lucide-react'

export type StepWizardStep = {
  key: string
  title: string
  description?: string
  content: ReactNode
  optional?: boolean
  disabled?: boolean
}

export type StepWizardProps = {
  steps: StepWizardStep[]
  activeStep: number
  onStepChange?: (index: number) => void
  completedSteps?: string[]
  className?: string
}

export function StepWizard({
  steps,
  activeStep,
  onStepChange,
  completedSteps = [],
  className = '',
}: StepWizardProps) {
  return (
    <div className={`step-wizard ${className}`.trim()}>
      <div className="step-wizard-nav">
        {steps.map((step, i) => {
          const isActive = i === activeStep
          const isCompleted = completedSteps.includes(step.key)
          const isClickable = !!onStepChange && (isCompleted || i < activeStep || i === activeStep)

          return (
            <button
              key={step.key}
              className={`step-wizard-nav-item${isActive ? ' step-wizard-nav-item--active' : ''}${isCompleted ? ' step-wizard-nav-item--completed' : ''}`}
              type="button"
              disabled={!isClickable}
              onClick={() => onStepChange?.(i)}
            >
              <span className="step-wizard-nav-num">
                {isCompleted ? <CircleCheck size={16} /> : i + 1}
              </span>
              <span className="step-wizard-nav-title">{step.title}</span>
              {step.description && (
                <small className="step-wizard-nav-desc">{step.description}</small>
              )}
            </button>
          )
        })}
      </div>
      <div className="step-wizard-body">
        {steps[activeStep]?.content}
      </div>
    </div>
  )
}