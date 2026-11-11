import { ttn_api } from "@/lib/axios"

export function saveAccount(account) {
  const accounts = JSON.parse(localStorage.getItem("ttn_accounts")) || {}
  localStorage.setItem("ttn_accounts", JSON.stringify({ ...accounts, [account.username]: account }))
  setActiveAccount(account.username)
}

export function removeAccount(username) {
  const accounts = JSON.parse(localStorage.getItem("ttn_accounts")) || {}
  delete accounts[username]
  localStorage.setItem("ttn_accounts", JSON.stringify(accounts))

  if (localStorage.getItem("ttn_active_account") === username) setActiveAccount(null)
  else window.dispatchEvent(new Event("ttn-accounts-change"))
}

export function getActiveAccount() {
  const accounts = JSON.parse(localStorage.getItem("ttn_accounts"))
  const username = localStorage.getItem("ttn_active_account")

  return accounts[username]
}

export function setActiveAccount(username) {
  localStorage.setItem("ttn_active_account", username)
  window.dispatchEvent(new Event("ttn-accounts-change"))
}

export function subscribeAccounts(listener) {
  function handleStorage(event) {
    if (!event.key || event.key === "ttn_accounts" || event.key === "ttn_active_account") listener()
  }

  window.addEventListener("storage", handleStorage)
  window.addEventListener("ttn-accounts-change", listener)

  return () => {
    window.removeEventListener("storage", handleStorage)
    window.removeEventListener("ttn-accounts-change", listener)
  }
}

export async function getAuthInfo(apiKey, signal) {
  const { data } = await ttn_api.get('/auth_info', {
    headers: { Authorization: `Bearer ${apiKey}` },
    signal,
  });
  if (!data.api_key?.entity_ids?.user_ids?.user_id) throw new Error("User not found")

  return data
}

export async function getLoginAccount(apiKey) {
  const auth = await getAuthInfo(apiKey)
  const username = auth.api_key.entity_ids.user_ids.user_id

  const { data: profile } = await ttn_api.get(`/users/${encodeURIComponent(username)}`, {
    params: { field_mask: 'name,profile_picture' },
    headers: { Authorization: `Bearer ${apiKey}` },
  })

  return {
    api_key: apiKey,
    username,
    name: profile?.name || username,
    image: profile?.profile_picture?.sizes?.[64]
  }
}
