import { describe, expect, it } from "vitest";

import { latencySeconds, retrievedTables } from "@/lib/answer-parts";

describe("Details helpers", () => {
  it("lists only the retrieved tables the SQL reads, not a table named like a column", () => {
    const record = {
      retrieved_tables: ["address.zip_data", "address.country", "address.zip_congress", "student_club.zip_code"],
      generated_sql:
        'SELECT "zip_data"."zip_code" FROM "address"."zip_data" AS "zip_data" INNER JOIN "address"."country" AS "country" ON 1 = 1',
    };
    expect(retrievedTables(record)).toEqual(["address.zip_data", "address.country"]);
  });

  it("keeps every retrieved table when there is no SQL", () => {
    expect(retrievedTables({ retrieved_tables: ["a.b", "a.c"] })).toEqual(["a.b", "a.c"]);
  });

  it("rounds the time to a tenth of a second", () => {
    expect(latencySeconds({ latency_sec: 34.52642893791199 })).toBe(34.5);
  });
});
