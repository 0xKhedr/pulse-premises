import { cn } from "cn"
import { LoaderCircleIcon, UserRoundIcon } from "lucide-react"
import RippleGrid from "@/components/ripple-grid"

export function AuthInfoPanel({ data, isPending }) {
  const apiKey = data?.api_key?.api_key;
  const userId = data?.api_key?.entity_ids?.user_ids?.user_id;
  const rights = apiKey?.rights || [];

  return (
    <div className={cn("relative min-w-0 border-t md:border-t-0 md:border-l", !isPending && !data && "hidden md:block")}>
      {isPending ? (
        <div className="flex h-72 items-center justify-center gap-2 p-6 text-sm text-muted-foreground md:absolute md:inset-0 md:h-auto" role="status">
          <LoaderCircleIcon className="size-4 animate-spin" aria-hidden="true" />
          Loading user details…
        </div>
      ) : data ? (
        <section className="grid h-72 min-h-0 grid-cols-2 gap-4 p-6 md:absolute md:inset-0 md:h-auto md:p-8" aria-labelledby="viewed-user-title" aria-live="polite">
          <div className="min-w-0 space-y-4 overflow-y-auto">
            <div className="flex items-center gap-3">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                <UserRoundIcon className="size-4" aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <h2 id="viewed-user-title" className="text-sm text-muted-foreground">User details</h2>
                <p className="break-all font-semibold">{userId}</p>
              </div>
            </div>
            <dl className="space-y-3 text-sm">
              <div>
                <dt className="text-muted-foreground">API key name</dt>
                <dd className="mt-1 break-words font-medium">{apiKey?.name || "Unnamed key"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Access</dt>
                <dd className="mt-1 font-medium">{data.is_admin ? "Administrator" : "User"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Expires</dt>
                <dd className="mt-1 font-medium">
                  {apiKey?.expires_at ? new Date(apiKey.expires_at).toLocaleString() : "No expiry"}
                </dd>
              </div>
            </dl>
          </div>
          <div className="flex min-h-0 min-w-0 flex-col border-l pl-4">
            <h3 className="mb-3 text-sm font-medium">Permissions ({rights.length})</h3>
            {rights.length ? (
              <ul className="min-h-0 flex-1 space-y-2 overflow-y-auto text-sm text-muted-foreground [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                {rights.map((right) => (
                  <li key={right} className="break-words capitalize">
                    {right.replace(/^RIGHT_/, "").replaceAll("_", " ").toLowerCase()}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">No permissions listed.</p>
            )}
          </div>
        </section>
      ) : (
        <div className="absolute inset-0" aria-hidden="true">
          <RippleGrid
            color="#000000"
            lightMode
            autoRipple="random"
            cellSize={60}
            rippleSpeed={2.5}
            rippleSize={0.2}
            rippleInterval={5}
            mouseInteraction={false}
            rotation={5}
          />
        </div>
      )}
    </div>
  );
}
