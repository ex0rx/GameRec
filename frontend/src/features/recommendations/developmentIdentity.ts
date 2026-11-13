/** Syntactic validation only; an identifier does not establish account ownership. */
export function validDevelopmentSteamId(value: string): boolean {
  const clean = value.trim()
  return /^[1-9][0-9]{0,19}$/.test(clean) && BigInt(clean) <= 18446744073709551615n
}
