/** A continuous, rounded B with two offset apertures: one team, connected work. */
export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-label="Botly" role="img">
      <rect width="64" height="64" rx="18" fill="#242424" />
      <path fillRule="evenodd" clipRule="evenodd" d="M18 14H33C42.4 14 48 18.5 48 25.5C48 29.3 46.1 32.4 42.8 34.2C47.4 35.8 50 39.2 50 43.5C50 51 43.9 55 34.5 55H18V14ZM27 22V31H32C36.8 31 39 29.4 39 26.5C39 23.5 36.8 22 32 22H27ZM27 38V47H33.5C38.5 47 41 45.6 41 42.5C41 39.5 38.5 38 33.5 38H27Z" fill="#FFFFFF" transform="translate(-2 -2)" />
      <path d="M12 32H26" stroke="#242424" strokeWidth="4" />
    </svg>
  );
}
