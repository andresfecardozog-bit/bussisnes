export interface AuthUser {
  id: number;
  email: string;
  full_name: string | null;
  must_change_password: boolean;
  roles: string[];
  permissions: string[];
}

export interface AuthMeResponse {
  ok: boolean;
  user: AuthUser;
  auth_kind: string;
}

export interface AuthLoginResponse {
  ok: boolean;
  user: AuthUser;
}
