import test from 'node:test';
import assert from 'node:assert/strict';
import { createJiti } from 'jiti';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const jiti = createJiti(import.meta.url, { alias: { '@': root } });
const q = await jiti.import('../lib/questionnaire.ts');
const { calculateCabinet } = await jiti.import('../lib/cabinet-engine.ts');
const { allocateControllerIo } = await jiti.import('../lib/io-allocator.ts');

function complete(withSensors = false) {
  const state = structuredClone(q.initialQuestionnaireState);
  for (let pass = 0; pass < 20; pass++) {
    for (const group of q.visibleQuestionGroups(state)) {
      const first = group.nodes[0];
      const key = `${first.sheet}:${first.order}`;
      if (['list', 'radio_group'].includes(group.fieldType)) {
        state.selected[group.key] ||= (group.nodes.find(node => node.answer === 'Насос') || first).order;
      } else if (group.fieldType === 'input') {
        const rule = q.numericInputRule(first.inputRule);
        state.inputs[key] = group.question.startsWith('Количество') ? '3' : rule ? String(rule.min) : 'M1';
      } else if (group.fieldType === 'check_box') {
        state.checks[key] = withSensors && ['Дополнительные параметры двигателя', 'Термоконтакт (биметаллический)', 'Датчик температуры обмотки Pt100'].includes(group.question);
      }
    }
  }
  assert.deepEqual(q.missingRequiredAnswers(state), []);
  return state;
}

test('direct motor has a single logical selection, load index and traced specification', () => {
  const result = calculateCabinet([{ id: 'one', tag: 'M1', state: complete() }]);
  assert.equal(result.canExport, true, JSON.stringify(result.errors));
  assert.equal(result.instances.length, 2);
  assert.ok(result.blocks[0].loadIndex);
  assert.ok(result.blocks[0].specification.length);
  assert.ok(result.blocks[0].specification.every(item => item.source));
});

test('two identical motors retain every occurrence and unique logical addresses', () => {
  const state = complete(true);
  const result = calculateCabinet([{ id: 'one', tag: 'M1', state }, { id: 'two', tag: 'M2', state: structuredClone(state) }]);
  assert.equal(result.canExport, true, JSON.stringify(result.errors));
  assert.equal(result.blocks[0].optional.length, 4); // thermal + 3 winding sensors
  assert.equal(result.instances.length, 20); // two template fragments per demand
  assert.equal(result.instances.filter(item => item.drawingKind === 'electrical').length, 10);
  assert.equal(result.instances.filter(item => item.drawingKind === 'external').length, 10);
  assert.equal(new Set(result.instances.map(item => item.id)).size, 20);
  const addresses = result.blocks.flatMap(block => block.channels.map(item => item.address));
  assert.equal(new Set(addresses).size, addresses.length);
  assert.ok(Object.values(result.io).reduce((sum, count) => sum + count, 0) > 4);
});

test('hidden sensor answers neither affect diagram selection nor survive pruning', () => {
  const state = complete(true);
  const group = q.visibleQuestionGroups(state).find(item => item.question === 'Дополнительные параметры двигателя');
  state.checks[group.key] = false;
  assert.deepEqual(q.selectedOptionalDiagramRows(state), []);
  const cleaned = q.pruneQuestionnaireState(state);
  assert.equal(Object.keys(cleaned.inputs).some(key => key.startsWith('Ques 2:')), false);
  assert.equal(Object.keys(cleaned.selected).some(key => key.startsWith('Ques 2:')), false);
});

test('incomplete cabinet cannot silently export only its completed motor', () => {
  const result = calculateCabinet([{ id: 'one', tag: 'M1', state: complete() }, { id: 'two', tag: 'M2', state: q.initialQuestionnaireState }]);
  assert.equal(result.canExport, false);
  assert.ok(result.errors.some(error => error.startsWith('M2:')));
});

test('changed visible options invalidate an old choice; invalid optional strings are checked', () => {
  const state = complete();
  const start = q.visibleQuestionGroups(state).find(item => item.nodes[0].engineKey === 'diagram1.start');
  state.selected[start.key] = 'not-in-workbook';
  assert.ok(q.missingRequiredAnswers(state).includes(start.question));
  const valid = complete();
  const marking = q.visibleQuestionGroups(valid).find(item => item.question.trim() === 'Маркировка');
  valid.inputs[marking.key] = 'x'.repeat(101);
  assert.ok(q.missingRequiredAnswers(valid).some(error => error.includes('длина')));
});

test('analog I/O counted; malformed or unknown I/O fields rejected', () => {
  const result = allocateControllerIo([{ id: 'sensor', candidates: [{ sourceRow: 1, io: { ai420: 2, ao010: 1 } }] }]);
  assert.equal(result.allocations[0].channels.length, 3);
  assert.ok(allocateControllerIo([{ id: 'bad', candidates: [{ sourceRow: 1, io: { di0: -1 } }] }]).error);
  assert.ok(allocateControllerIo([{ id: 'bad', candidates: [{ sourceRow: 1, io: { unknown: 1 } }] }]).error);
});
