/**
 * The shape rules for the two patch fields a steward types by hand, mirrored from the engine.
 *
 * `src/governed_bi/feedback/validate.py::_patch_problems` is the authority and this is a copy of it,
 * which is a cost worth naming: the form has to answer at the keystroke and a round trip cannot.
 * The copy is confined to this module so there is one place to reconcile, and
 * `scripts/check-patch-fields.ts` pins the two rules against the wording the server uses.
 *
 * Its own module rather than a helper inside `api-client.ts` for a duller reason: `api-client.ts`
 * uses a constructor parameter property, which Node's `--experimental-strip-types` refuses, so
 * nothing there can be reached from a check script.
 */

/** The full digest, not the 16-character prefix every display uses. */
export const HASH_CHARS = 64;

const HEX = /^[0-9a-f]+$/;

/**
 * Why the steward cannot submit this hash, or `null` if they can.
 *
 * Two rules in the server's order, and they stay two messages because they imply different fixes:
 * a short hash means the display prefix was pasted, a non-hex one means it is not a digest at all.
 * The form used to check the length alone, so 64 characters of `z` passed here and were refused by
 * the engine -- and `validate.py` carries a comment recording that the server shipped the same bug
 * first, reaching its hex rule only when the length happened to be right.
 *
 * An empty field is not an error. Nothing has been typed yet, and a form that shouts before the
 * first keystroke trains its reader to ignore it.
 */
export function patchHashProblem(hash: string): string | null {
  if (hash === "") return null;
  if (hash.length !== HASH_CHARS) {
    return (
      `${hash.length} characters, and a corpus content hash is ${HASH_CHARS}. A truncated one — ` +
      "the 16-character prefix every display uses — never equals the digest the landing check " +
      "compares it against, so the patch would report `superseded` while nothing had changed."
    );
  }
  if (!HEX.test(hash)) {
    return (
      `${HASH_CHARS} characters, but not hex. A digest is lowercase ` +
      "`0-9a-f`; this would be stored and then never match the corpus it claims to describe."
    );
  }
  return null;
}
