/**
 * Browser-generated instance ids are only used to distinguish blocks inside
 * the current project. `crypto.randomUUID` is unavailable on plain HTTP, so
 * the public MVP needs a fallback until it is served over HTTPS.
 */
export function createClientId(
  randomUuid?: (() => string) | null,
) {
  const nativeRandomUuid =
    randomUuid === undefined
      ? globalThis.crypto?.randomUUID?.bind(globalThis.crypto)
      : randomUuid;
  if (nativeRandomUuid) return nativeRandomUuid();

  const randomPart = Math.random().toString(36).slice(2);
  return `local-${Date.now().toString(36)}-${randomPart}`;
}

/** A copied functional block must never share a mutable questionnaire state. */
export function cloneQuestionnaireState<T>(state: T): T {
  return structuredClone(state);
}
