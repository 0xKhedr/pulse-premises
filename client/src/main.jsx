import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate } from "react-router"
import { RouterProvider } from "react-router/dom"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import './index.css'
import App from './App.jsx'
import Gateways from './pages/Gateways.jsx'
import Devices from './pages/Devices.jsx'
import Premises from './pages/Premises.jsx'
import Provision from './pages/Provision.jsx'
import Auth from './pages/Auth.jsx'
import { ProtectedRoute } from './components/protected-route.jsx'

const queryClient = new QueryClient();

const router = createBrowserRouter([
  {
    path: "/auth",
    element: <Auth />,
  },
  {
    path: "/",
    element: <ProtectedRoute><App /></ProtectedRoute>,
    children: [
      { index: true, element: <Navigate to="/gateways" replace /> },
      {
        path: "gateways",
        handle: { title: "Gateways", group: "Manage" },
        element: <Gateways />,
      },
      {
        path: "devices",
        handle: { title: "Devices", group: "Manage" },
        element: <Devices />,
      },
      {
        path: "premises",
        handle: { title: "Premises", group: "Manage" },
        element: <Premises />,
      },
      {
        path: "provision",
        handle: { title: "Provision", group: "Control" },
        element: <Provision />,
      },
    ],
  },
]);

createRoot(document.getElementById('root')).render(
  <QueryClientProvider client={queryClient}>
    <RouterProvider router={router} />
  </QueryClientProvider>
);
