type BrandLockupProps = {
  className?: string
  logoSrc?: string
}

export function BrandLockup({ className = "", logoSrc = "/assets/databricks-logo.svg" }: BrandLockupProps) {
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <img src={logoSrc} alt="Databricks" className="h-9 w-9 shrink-0" />
      <div className="leading-none">
        <div className="text-[18px] font-semibold tracking-tight text-white">databricks</div>
        <div className="mt-1 text-[11px] font-bold uppercase tracking-[0.28em] text-[#FF5F1F]">Quest</div>
      </div>
    </div>
  )
}
