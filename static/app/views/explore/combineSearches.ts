import type {MutableSearch} from 'sentry/utils/tokenizeSearch';

/**
 * Merges an optional secondary search into the primary by appending its tokens.
 */
export function combineSearches(
  base: MutableSearch,
  added: MutableSearch | undefined
): MutableSearch {
  if (!added) {
    return base;
  }

  const combined = base.copy();
  combined.tokens.push(...added.tokens);
  return combined;
}
