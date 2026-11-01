import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate } from "react-router";
import { RouterProvider } from "react-router/dom";
import './index.css'
import App from './App.jsx'
import Gateways from './pages/Gateways.jsx'
import Devices from './pages/Devices.jsx'
import Buildings from './pages/Buildings.jsx'
import Provisioning from './pages/Provisioning.jsx'

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
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
        path: "buildings",
        handle: { title: "Buildings", group: "Manage" },
        element: <Buildings />,
      },
      {
        path: "provisioning",
        handle: { title: "Provisioning", group: "Control" },
        element: <Provisioning />,
      },
    ],
  },
]);

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
