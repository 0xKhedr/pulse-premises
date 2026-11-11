import { Link, useLocation } from "react-router"

import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar"

export function NavMain({
  title,
  items
}) {
  const { pathname } = useLocation()
  const { isMobile, setOpenMobile } = useSidebar()

  return (
    <SidebarGroup>
      <SidebarGroupLabel>{title}</SidebarGroupLabel>
      <SidebarMenu>
        {items.map((item) => (
          <SidebarMenuItem key={item.title}>
            <SidebarMenuButton
              tooltip={item.title}
              className="font-[450] data-active:font-[450] text-sidebar-foreground/65 [&_svg]:text-sidebar-foreground/65 hover:text-sidebar-foreground active:text-sidebar-foreground data-active:bg-sidebar-foreground/6 data-active:hover:bg-sidebar-foreground/6 data-active:active:bg-sidebar-foreground/6 data-active:text-sidebar-foreground data-active:hover:text-sidebar-foreground data-active:active:text-sidebar-foreground data-active:[&_svg]:text-sidebar-foreground"
              isActive={pathname === item.url || pathname.startsWith(`${item.url}/`)}
              render={<Link to={item.url} />}
              onClick={() => {
                if (isMobile) setOpenMobile(false)
              }}
              aria-current={pathname === item.url ? "page" : undefined}
            >
              {item.icon}
              <span>{item.title}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
        ))}
      </SidebarMenu>
    </SidebarGroup>
  )
}
