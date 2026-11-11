import { Navigate, useLocation } from "react-router"
import { useAuth } from "@/hooks/use-auth"

export function ProtectedRoute({ children }) {
  const location = useLocation();
  const auth = useAuth();

  if (!auth.activeAccount || auth.isError) {
    return (
      <Navigate
        to='/auth'
        state={{
          from: location.pathname + location.search + location.hash,
          error: auth.error?.response?.data?.message || auth.error?.message,
        }}
        replace
      />
    );
  }

  if (auth.isPending) {
    return <p role="status" className="p-6 text-center">Checking your session…</p>;
  }

  return children;
}
