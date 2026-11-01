import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { createBrowserRouter, Navigate } from "react-router";
import { RouterProvider } from "react-router/dom";
import './index.css'
import App from './App.jsx'

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Navigate to="/gateways" replace /> },
      ...[
        { path: "gateways", title: "Gateways", group: "Manage" },
        { path: "devices", title: "Devices", group: "Manage" },
        { path: "buildings", title: "Buildings", group: "Manage" },
        { path: "provisioning", title: "Provisioning", group: "Control" },
      ].map(({ path, title, group }) => ({
        path,
        handle: { title, group },
        element: <h1 className="text-2xl font-semibold">{title}</h1>,
      })),
    ],
  },
]);

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
