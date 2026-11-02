import { createContext, useContext, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type { User } from "./types";

interface AuthCtx { user: User | null; loading: boolean; refresh: () => void; logout: () => Promise<void> }
const Ctx = createContext<AuthCtx>({ user: null, loading: true, refresh: () => {}, logout: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<User>("/api/auth/me").catch(() => null),
    refetchInterval: 15000,
  });
  const value: AuthCtx = {
    user: q.data ?? null,
    loading: q.isLoading,
    refresh: () => qc.invalidateQueries({ queryKey: ["me"] }),
    logout: async () => {
      await api.post("/api/auth/logout");
      qc.clear();
      window.location.href = "/login";
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

export const canWrite = (u: User | null) => !!u && ["analyst", "ops_manager", "regional_head", "director"].includes(u.role);
export const canApprove = (u: User | null) => !!u && ["ops_manager", "regional_head", "director"].includes(u.role);
