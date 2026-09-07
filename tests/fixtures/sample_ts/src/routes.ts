import { AuthController } from "./controller";

const controller = new AuthController();

export function handleLoginRequest(userId: string, password: string): boolean {
  return controller.login(userId, password);
}

export function handleRefreshRequest(sessionId: string): Promise<boolean> {
  return controller.refresh(sessionId);
}
