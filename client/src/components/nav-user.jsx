import {
  Avatar,
  AvatarFallback,
  AvatarImage,
} from "@/components/ui/avatar"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"
import { RiExpandUpDownLine, RiVerifiedBadgeLine, RiBankCardLine, RiNotification3Line, RiLogoutBoxRLine, RiUserSharedLine, RiUserSettingsLine, RiCheckLine } from "@remixicon/react"

import { useNavigate } from "react-router"
import { useAccounts, useActiveAccount, useAccountActions } from "@/hooks/use-auth"

export function NavUser() {
  const { isMobile } = useSidebar();
  const navigate = useNavigate();
  const accounts = useAccounts();
  const activeAccount = useActiveAccount();
  const { logout, switchAccount } = useAccountActions();
  const user = {
    name: activeAccount?.name || activeAccount?.username || 'User',
    username: activeAccount?.username || '',
    avatar: activeAccount?.image || '',
  };
  const initials = (user.name || user.username || 'U').trim().split(/\s+/).map((part) => part[0]).slice(0, 2).join('').toUpperCase();

  async function handleLogout() {
    await logout();
    navigate('/auth', { replace: true });
  }

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <SidebarMenuButton size="lg" className="data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground" />
            }
          >
            <Avatar className="rounded-lg after:rounded-lg">
              <AvatarImage className="rounded-lg" src={user.avatar} alt={user.name} />
              <AvatarFallback className="rounded-lg">{initials}</AvatarFallback>
            </Avatar>
            <div className="grid flex-1 text-left text-sm leading-tight">
              <span className="truncate font-medium">{user.name}</span>
              <span className="truncate text-xs">{user.username}</span>
            </div>
            <RiExpandUpDownLine className="ml-auto size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="min-w-56 rounded-lg"
            side={isMobile ? "bottom" : "right"}
            align="end"
            sideOffset={4}
          >
            <DropdownMenuGroup>
              <DropdownMenuItem>
                <RiVerifiedBadgeLine
                />
                Account
              </DropdownMenuItem>
              <DropdownMenuItem>
                <RiBankCardLine
                />
                Billing
              </DropdownMenuItem>
              <DropdownMenuItem>
                <RiNotification3Line
                />
                Notifications
              </DropdownMenuItem>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuSub>
              <DropdownMenuSubTrigger>
                <RiUserSharedLine />
                Switch Accounts
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent className="min-w-56">
                <DropdownMenuGroup>
                  {Object.values(accounts).map((account) => (
                    <DropdownMenuItem
                      key={account.username}
                      onClick={() => switchAccount(account.username)}
                      className="gap-2 py-2"
                      aria-label={`${account.username}${account.username === activeAccount?.username ? ' (current account)' : ''}`}
                    >
                      <Avatar className="size-7 rounded-lg after:rounded-lg">
                        <AvatarImage src={account.image} alt="" className="rounded-lg" />
                        <AvatarFallback className="rounded-lg text-xs">
                          {account.username.slice(0, 2).toUpperCase()}
                        </AvatarFallback>
                      </Avatar>
                      <span className="flex-1 truncate">{account.username}</span>
                      {account.username === activeAccount?.username && <RiCheckLine aria-hidden="true" />}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem disabled>
                  <RiUserSettingsLine />
                  Manage Accounts
                </DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuItem onClick={handleLogout}>
              <RiLogoutBoxRLine
              />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  );
}
