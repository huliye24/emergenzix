export function canMutateApi(
  pathname: string,
  method: string,
  operatorAuthenticated: boolean,
): boolean {
  return operatorAuthenticated
    && pathname === "/api/v1/backtests"
    && method.toUpperCase() === "POST";
}
