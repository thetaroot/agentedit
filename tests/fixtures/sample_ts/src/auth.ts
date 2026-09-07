export interface User {
  id: string;
  name: string;
}

export function validateUser(u: User): boolean {
  return u.id.length > 0;
}

export function authenticate(userId: string, token: string): boolean {
  const ok = validateUser({ id: userId, name: "x" });
  return ok && token.length > 0;
}

export async function refreshSession(sessionId: string): Promise<boolean> {
  return sessionId !== "";
}
