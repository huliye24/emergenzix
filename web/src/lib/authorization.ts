export function canMutateApi(
  pathname: string,
  method: string,
  operatorAuthenticated: boolean,
): boolean {
  return operatorAuthenticated
    && new Set(["/api/v1/backtests", "/api/v1/datasets"]).has(pathname)
    && method.toUpperCase() === "POST";
}
