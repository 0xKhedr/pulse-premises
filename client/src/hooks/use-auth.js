import { useMemo, useSyncExternalStore } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"

import {
  getAuthInfo,
  getLoginAccount,
  removeAccount,
  saveAccount,
  setActiveAccount,
  subscribeAccounts,
} from "@/lib/auth"

function useStoredValue(key) {
  return useSyncExternalStore(subscribeAccounts, () => localStorage.getItem(key))
}

export function useAccounts() {
  const snapshot = useStoredValue("ttn_accounts")

  return useMemo(() => JSON.parse(snapshot) || {}, [snapshot])
}

export function useActiveAccount() {
  const accounts = useAccounts()
  const username = useStoredValue("ttn_active_account")

  return accounts[username]
}

export function useAccountActions() {
  const queryClient = useQueryClient()

  async function updateAccount(update) {
    await queryClient.cancelQueries()
    queryClient.clear()
    update()
  }

  return {
    async login(apiKey) {
      const account = await getLoginAccount(apiKey)
      await updateAccount(() => saveAccount(account))
    },
    logout () {
      updateAccount(() => removeAccount(localStorage.getItem("ttn_active_account")))
    },
    switchAccount (username) {
      if (username !== localStorage.getItem("ttn_active_account")) return updateAccount(() => setActiveAccount(username))
    },
  }
}

export function useAuth() {
  const activeAccount = useActiveAccount()
  const apiKey = activeAccount?.api_key

  const auth = useQuery({
    queryKey: ['auth', activeAccount?.username, apiKey],
    queryFn: ({ signal }) => getAuthInfo(apiKey, signal),
    enabled: Boolean(apiKey),
    retry: false,
    staleTime: 60000,
  })

  return { ...auth, activeAccount }
}
