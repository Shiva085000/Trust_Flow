import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DemoProvider } from "./demo/DemoContext";
import { AuthProvider } from "./contexts/AuthContext";
import App from "./App";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 2,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthProvider>
      <QueryClientProvider client={queryClient}>
        <DemoProvider>
          <App />
        </DemoProvider>
      </QueryClientProvider>
    </AuthProvider>
  </React.StrictMode>
);
