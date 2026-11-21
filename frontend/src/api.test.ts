import { describe, expect, it, vi } from "vitest";
import { api, ApiFailure } from "./api";

describe("API client", () => {
  it("propagates a structured server error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: "denied",
            code: "forbidden",
            request_id: "r",
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    await expect(api.me()).rejects.toBeInstanceOf(ApiFailure);
  });
});
