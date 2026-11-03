import {
  AudioLinesIcon,
  Building2Icon,
  CpuIcon,
  GalleryVerticalEndIcon,
  RouterIcon,
  TerminalIcon,
  WrenchIcon,
} from "lucide-react"

import { NavMain } from "@/components/nav-main"
import { NavUser } from "@/components/nav-user"
import { TeamSwitcher } from "@/components/team-switcher"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarRail,
} from "@/components/ui/sidebar"

// Replace the sample account and teams when authentication is connected.
const data = {
  user: {
    name: "shadcn",
    email: "m@example.com",
    avatar: "/avatars/shadcn.jpg",
  },
  teams: [
    { name: "Acme Inc", logo: <GalleryVerticalEndIcon />, plan: "Enterprise" },
    { name: "Acme Corp.", logo: <AudioLinesIcon />, plan: "Startup" },
    { name: "Evil Corp.", logo: <TerminalIcon />, plan: "Free" },
  ],
  manage: [
    { title: "Gateways", url: "/gateways", icon: <RouterIcon /> },
    { title: "Devices", url: "/devices", icon: <CpuIcon /> },
    { title: "Premises", url: "/premises", icon: <Building2Icon /> },
  ],
  control: [
    { title: "Provision", url: "/provision", icon: <WrenchIcon /> },
  ],
}

export function AppSidebar({ ...props }) {
  return (
    <Sidebar collapsible="icon" {...props}>
      <SidebarHeader>
        <TeamSwitcher teams={data.teams} />
      </SidebarHeader>
      <SidebarContent>
        <NavMain title="Manage" items={data.manage} />
        <NavMain title="Control" items={data.control} />
      </SidebarContent>
      <SidebarFooter>
        <NavUser user={data.user} />
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  )
}
