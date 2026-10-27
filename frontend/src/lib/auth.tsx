"use client";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, tokenStore, type Me } from "./api";

interface AuthState { me: Me | null; loading: boolean; login: (token: string) => Promise<Me>; logout: () => void }
const Ctx = createContext<AuthState | null>(null);

export function homeFor(role: Me["role"]): string {
  return role === "applicant" ? "/apply" : role === "admin" ? "/admin" : "/review";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = tokenStore.get();
    if (!t) { setLoading(false); return; }
    api<Me>("/me").then(setMe).catch(() => tokenStore.clear()).finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (token: string) => {
    const m = await api<Me>("/me", {}, token.trim());       // throws ApiError(401) for a wrong token
    tokenStore.set(token.trim());
    setMe(m);
    return m;
  }, []);

  const logout = useCallback(() => { tokenStore.clear(); setMe(null); }, []);
  return <Ctx.Provider value={{ me, loading, login, logout }}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const c = useContext(Ctx);
  if (!c) throw new Error("useAuth outside AuthProvider");
  return c;
}

/** Redirects to the login page unless signed in with one of the roles; returns the user when allowed. */
export function useRequireRole(...roles: Me["role"][]): { me: Me | null; ready: boolean } {
  const { me, loading } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (loading) return;
    if (!me) router.replace("/");
    else if (!roles.includes(me.role)) router.replace(homeFor(me.role));
  }, [me, loading, router, roles]);
  return { me, ready: !loading && !!me && roles.includes(me.role) };
}

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : e instanceof Error ? e.message : "Something went wrong";
}
