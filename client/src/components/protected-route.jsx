import { useQuery } from "@tanstack/react-query"
import { Navigate, useLocation } from "react-router"
import Cookies from "js-cookie"

import { ttn_api } from "@/lib/axios"

export function ProtectedRoute({ children }) {
  const location = useLocation();
  const api_key = Cookies.get('ttn_api_key');

  const auth = useQuery({
    queryKey: ['auth'],
    queryFn: async () => {
      const { data } = await ttn_api.get('/auth_info');
      if (!data.api_key?.entity_ids?.user_ids?.user_id) throw new Error("User not found");

      return data;
    },
    enabled: Boolean(api_key),
    retry: false,
    staleTime: 60000,
  });

  if (!api_key || auth.isError) {
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
