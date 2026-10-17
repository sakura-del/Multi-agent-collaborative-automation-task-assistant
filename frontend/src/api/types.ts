// 后端契约类型：与 FastAPI 的 Pydantic 输出字段保持一致（snake_case）
export interface UserOut {
  id: string;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface TokenOut {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginInput {
  account: string;
  password: string;
}

export interface RegisterInput {
  username: string;
  email: string;
  password: string;
}
