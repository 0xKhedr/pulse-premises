import {
  RiBuildingLine,
  RiCpuLine,
  RiDashboardLine,
  RiRouterLine,
  RiToolsLine,
} from "@remixicon/react"
import { Link } from "react-router"

import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"

const data = {
  monitor: [
    { title: "Dashboard", url: "/dashboard", icon: <RiDashboardLine /> },
  ],
  manage: [
    { title: "Gateways", url: "/gateways", icon: <RiRouterLine /> },
    { title: "Devices", url: "/devices", icon: <RiCpuLine /> },
    { title: "Premises", url: "/premises", icon: <RiBuildingLine /> },
  ],
  control: [
    { title: "Provision", url: "/provision", icon: <RiToolsLine /> },
  ],
}

export function AppSidebar({ ...props }) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link to="/dashboard" />}>
              <div className="flex aspect-square size-8 p-px shrink-0 items-center justify-center rounded-lg bg-black">
                <img src="/logo.svg" alt="" className="invert" />
              </div>
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">Pulse Premises</span>
                <span className="truncate text-xs text-muted-foreground">Observe every signal</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        <NavMain title="Monitor" items={data.monitor} />
        <NavMain title="Manage" items={data.manage} />
        <NavMain title="Control" items={data.control} />
      </SidebarContent>
      <SidebarFooter>
        <NavUser />
      </SidebarFooter>
    </Sidebar>
  )
}
