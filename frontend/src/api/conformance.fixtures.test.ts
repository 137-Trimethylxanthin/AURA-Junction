/// <reference types="node" />
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { CONTRACT_VERSION } from './contract'
import { CONFORMANCE, MOCK_REGION } from './conformance.fixtures'

/**
 * conformance.fixtures.json is the backend's copy of the golden table (backend/tests/test_contract.py reads it).
 * Regenerate with `npm run fixtures:export`; this test fails whenever the JSON and the .ts drift apart.
 */
const JSON_PATH = resolve(import.meta.dirname, 'conformance.fixtures.json')

const expected = () =>
  JSON.stringify(
    {
      contract_version: CONTRACT_VERSION,
      drawing_id: 'swiss-46639',
      generated_from: 'frontend/src/api/conformance.fixtures.ts',
      mock_region: MOCK_REGION,
      cases: CONFORMANCE,
    },
    null,
    2,
  ) + '\n'

describe('conformance.fixtures.json', () => {
  it('matches conformance.fixtures.ts', () => {
    if (process.env.UPDATE_FIXTURES === '1') writeFileSync(JSON_PATH, expected())
    expect(existsSync(JSON_PATH), 'run `npm run fixtures:export`').toBe(true)
    expect(readFileSync(JSON_PATH, 'utf8')).toBe(expected())
  })
})
