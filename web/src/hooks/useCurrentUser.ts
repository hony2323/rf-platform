import { useQuery } from "@tanstack/react-query";
import { getMe } from "../api/auth";
import { UnauthorizedError } from "../api/client";
import type { UserResponse } from "../types/api";

export function useCurrentUser() {
  return useQuery<UserResponse, Error>({
    queryKey: ["me"],
    queryFn: getMe,
    retry: (failureCount, error) => {
      if (error instanceof UnauthorizedError) return false;
      return failureCount < 2;
    },
  });
}
