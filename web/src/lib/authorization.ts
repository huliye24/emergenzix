export function canMutateApi(
  pathname: string,
  method: string,
  operatorAuthenticated: boolean,
): boolean {
  return operatorAuthenticated
    && new Set([
      "/api/v1/backtests",
      "/api/v1/datasets",
      "/api/v1/turtle-matrices",
    ]).has(pathname)
    && method.toUpperCase() === "POST";
}
