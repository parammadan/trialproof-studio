export const Logo = ({ size = 22 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
    <rect width="32" height="32" rx="8" fill="var(--surface-3)" />
    <rect x=".5" y=".5" width="31" height="31" rx="7.5" fill="none" stroke="var(--line)" />
    <path d="M9 10h14M16 10v13" stroke="var(--accent)" strokeWidth="2.6" strokeLinecap="round" />
    <circle cx="23" cy="22" r="3.2" fill="var(--ok)" />
  </svg>
)
