import { describe, expect, it } from "vitest";

import { triedFirst } from "@/lib/answer-parts";
import { formatSql, sameTokens } from "@/lib/sql-format";

function record(attempts: unknown[]) {
  return { execution: { attempts } };
}

describe("triedFirst", () => {
  it("lists every failure when nothing answered, and skips the cap row", () => {
    const rows = triedFirst(
      record([
        { path: "agent", passed: false, reason_code: "r_star_projection", submitted_sql: "SELECT *" },
        { path: "agent", passed: false, reason_code: "attempt_cap" },
      ]),
    );
    expect(rows).toEqual([
      { sql: "SELECT *", sentence: "The checks stopped it: it asked for every column with *." },
    ]);
  });

  it("drops what came after the answering attempt and reads an old record with no submitted SQL", () => {
    const rows = triedFirst(
      record([
        { path: "agent", passed: false, reason_code: "r_new_rule" },
        { path: "agent", passed: true, executed_sql: "SELECT 1" },
        { path: "agent", passed: false, reason_code: "r_not_a_read", submitted_sql: "DELETE" },
      ]),
    );
    expect(rows).toEqual([{ sql: null, sentence: "The checks stopped it: it broke one of the rules." }]);
  });
});

describe("formatSql", () => {
  it("changes only whitespace", () => {
    const sql =
      'SELECT a, b FROM "s"."t" WHERE a = \'x, y\' AND b IN (SELECT c FROM u) ORDER BY a LIMIT 5';
    const formatted = formatSql(sql);
    expect(formatted).not.toBe(sql);
    expect(sameTokens(sql, formatted)).toBe(true);
    expect(formatted.split("\n")).toContain("WHERE a = 'x, y'");
  });

  it("leaves short SQL alone", () => {
    expect(formatSql("SELECT 1")).toBe("SELECT 1");
  });
});
