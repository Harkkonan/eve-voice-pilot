/**
 * EVE Google Sheets tools for Brian's market workbook.
 *
 * Install in the spreadsheet Apps Script project as Code.gs.
 * Requires:
 * - GESI installed in the spreadsheet for wallet custom functions.
 * - Character authorized in GESI with esi-wallet.read_character_wallet.v1.
 */

const EVE_SHEET = Object.freeze({
  MARKET_TOOL: 'Market Tool',
  WALLET_IMPORT: 'Wallet Import',
  PURCHASE_LEDGER: 'Purchase Ledger',
  PURCHASE_GROUPS: 'Purchase Groups',
  MODULE_SPEND: 'Module Spend Summary',
  TYPE_CACHE: 'Type Cache',
});

const EVE_HEADERS = Object.freeze({
  LEDGER: [
    'transaction_id',
    'date',
    'type_id',
    'item_name',
    'category',
    'group',
    'is_ship',
    'is_module_or_fit_item',
    'is_buy',
    'quantity',
    'unit_price',
    'total_isk',
    'location_id',
    'location_name',
    'client_id',
    'is_personal',
    'journal_ref_id',
    'fit_group_id',
    'fit_group_start',
    'fit_group_end',
    'ship_candidate',
    'imported_at',
    'notes',
  ],
  GROUPS: [
    'fit_group_id',
    'group_start',
    'group_end',
    'minutes',
    'purchase_count',
    'ship_candidate',
    'ship_cost',
    'module_fit_cost',
    'other_cost',
    'total_cost',
    'location_names',
    'items_preview',
    'confidence',
    'notes',
  ],
  TYPE_CACHE: [
    'type_id',
    'item_name',
    'group_id',
    'group',
    'category_id',
    'category',
    'volume',
    'packaged_volume',
    'last_updated',
  ],
});

function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('EVE Market')
    .addItem('Load Market Tool Table', 'loadMarketToolTable')
    .addToUi();

  ui.createMenu('EVE Wallet')
    .addItem('Set Up Wallet Sheets', 'setupWalletSheets')
    .addItem('Append Wallet Transactions', 'appendWalletTransactions')
    .addItem('Rebuild Purchase Groups', 'rebuildPurchaseGroups')
    .addToUi();
}

function setupWalletSheets() {
  ensureWalletWorkbook_();
  SpreadsheetApp.getUi().alert(
    'Wallet sheets are ready. If Wallet Import shows an auth error, authorize the character in GESI with wallet access, then run EVE Wallet > Append Wallet Transactions.'
  );
}

function ensureWalletWorkbook_() {
  const ss = SpreadsheetApp.getActive();
  const importSheet = ensureSheet_(ss, EVE_SHEET.WALLET_IMPORT);
  const ledger = ensureSheet_(ss, EVE_SHEET.PURCHASE_LEDGER);
  const groups = ensureSheet_(ss, EVE_SHEET.PURCHASE_GROUPS);
  const spend = ensureSheet_(ss, EVE_SHEET.MODULE_SPEND);
  const cache = ensureSheet_(ss, EVE_SHEET.TYPE_CACHE);

  setupWalletImportSheet_(importSheet);
  setupHeaderSheet_(ledger, EVE_HEADERS.LEDGER);
  setupHeaderSheet_(groups, EVE_HEADERS.GROUPS);
  setupHeaderSheet_(cache, EVE_HEADERS.TYPE_CACHE);
  setupModuleSpendSheet_(spend);
}

function appendWalletTransactions() {
  ensureWalletWorkbook_();

  const ss = SpreadsheetApp.getActive();
  const importSheet = ss.getSheetByName(EVE_SHEET.WALLET_IMPORT);
  const ledger = ss.getSheetByName(EVE_SHEET.PURCHASE_LEDGER);

  SpreadsheetApp.flush();
  const imported = readWalletImportRows_(importSheet);
  if (!imported.length) {
    SpreadsheetApp.getUi().alert(
      'No wallet transactions were found on Wallet Import. Check that the GESI formula has loaded and that your EVE character is authorized for wallet transactions.'
    );
    return;
  }

  const existingIds = new Set(readColumnValues_(ledger, 1, 2).map(String));
  const buyRows = imported
    .filter(row => truthy_(row.is_buy))
    .filter(row => row.transaction_id && !existingIds.has(String(row.transaction_id)));

  if (!buyRows.length) {
    SpreadsheetApp.getUi().alert('No new purchase transactions to append.');
    return;
  }

  const typeInfo = getTypeInfoMap_(buyRows.map(row => row.type_id));
  const locationNames = resolveEsiNames_(buyRows.map(row => row.location_id));
  const importedAt = new Date();

  const rows = buyRows.map(row => {
    const type = typeInfo.get(String(row.type_id)) || {};
    const total = Number(row.quantity || 0) * Number(row.unit_price || 0);
    const category = type.category || '';
    const group = type.group || '';
    const isShip = category === 'Ship';
    const isFitItem = /^(Module|Charge|Drone|Subsystem|Fighter|Implant|Deployable|Structure Module)$/i.test(category);

    return [
      row.transaction_id,
      parseEveDate_(row.date),
      row.type_id,
      type.item_name || row.type_id,
      category,
      group,
      isShip,
      isFitItem,
      true,
      Number(row.quantity || 0),
      Number(row.unit_price || 0),
      total,
      row.location_id,
      locationNames.get(String(row.location_id)) || row.location_id || '',
      row.client_id || '',
      truthy_(row.is_personal),
      row.journal_ref_id || '',
      '',
      '',
      '',
      '',
      importedAt,
      '',
    ];
  });

  appendRows_(ledger, rows);
  formatLedger_(ledger);
  rebuildPurchaseGroups();

  SpreadsheetApp.getUi().alert(`Appended ${rows.length} new purchase transaction(s). Old purchase rows were preserved.`);
}

function rebuildPurchaseGroups() {
  const ss = SpreadsheetApp.getActive();
  const ledger = ensureSheet_(ss, EVE_SHEET.PURCHASE_LEDGER);
  setupHeaderSheet_(ledger, EVE_HEADERS.LEDGER);

  const rows = readObjects_(ledger, EVE_HEADERS.LEDGER.length);
  const purchases = rows
    .filter(row => row.transaction_id && truthy_(row.is_buy))
    .map(row => ({
      row,
      date: parseEveDate_(row.date),
      total: Number(row.total_isk || 0),
    }))
    .filter(item => item.date && !isNaN(item.date.getTime()))
    .sort((a, b) => a.date.getTime() - b.date.getTime());

  const groups = [];
  let current = null;

  purchases.forEach(item => {
    const gapMinutes = current
      ? (item.date.getTime() - current.lastDate.getTime()) / 60000
      : Infinity;
    if (!current || gapMinutes > 15) {
      current = { items: [], start: item.date, lastDate: item.date };
      groups.push(current);
    }
    current.items.push(item);
    current.lastDate = item.date;
  });

  const groupRows = [];
  const ledgerUpdates = new Map();
  groups.forEach((group, index) => {
    const id = makeGroupId_(group.start, index + 1);
    const end = group.lastDate;
    const minutes = Math.round((end.getTime() - group.start.getTime()) / 60000);
    const shipItems = group.items.filter(item => String(item.row.category) === 'Ship');
    const ship = shipItems.sort((a, b) => b.total - a.total)[0];
    const shipName = ship ? ship.row.item_name : '';
    const shipCost = sum_(shipItems.map(item => item.total));
    const fitCost = sum_(group.items.filter(item => truthy_(item.row.is_module_or_fit_item)).map(item => item.total));
    const totalCost = sum_(group.items.map(item => item.total));
    const otherCost = totalCost - shipCost - fitCost;
    const locations = unique_(group.items.map(item => item.row.location_name).filter(Boolean)).join(', ');
    const preview = group.items
      .slice()
      .sort((a, b) => b.total - a.total)
      .slice(0, 8)
      .map(item => `${item.row.quantity}x ${item.row.item_name}`)
      .join('; ');
    const confidence = shipName && group.items.length >= 2 && minutes <= 15
      ? 'High'
      : group.items.length >= 3 && minutes <= 15
        ? 'Medium'
        : 'Low';

    groupRows.push([
      id,
      group.start,
      end,
      minutes,
      group.items.length,
      shipName,
      shipCost,
      fitCost,
      otherCost,
      totalCost,
      locations,
      preview,
      confidence,
      '',
    ]);

    group.items.forEach(item => {
      ledgerUpdates.set(String(item.row.transaction_id), {
        fit_group_id: id,
        fit_group_start: group.start,
        fit_group_end: end,
        ship_candidate: shipName,
      });
    });
  });

  writeLedgerGroupUpdates_(ledger, ledgerUpdates);
  writePurchaseGroups_(ss, groupRows);
  rebuildModuleSpendSummary();
}

function rebuildModuleSpendSummary() {
  const ss = SpreadsheetApp.getActive();
  const spend = ensureSheet_(ss, EVE_SHEET.MODULE_SPEND);
  const ledger = ensureSheet_(ss, EVE_SHEET.PURCHASE_LEDGER);
  const rows = readObjects_(ledger, EVE_HEADERS.LEDGER.length)
    .filter(row => row.transaction_id && truthy_(row.is_buy));

  const byItem = new Map();
  rows.forEach(row => {
    const key = [row.item_name, row.category, row.group].join('\t');
    if (!byItem.has(key)) {
      byItem.set(key, {
        item_name: row.item_name,
        category: row.category,
        group: row.group,
        quantity: 0,
        total: 0,
        count: 0,
        last: null,
      });
    }
    const entry = byItem.get(key);
    entry.quantity += Number(row.quantity || 0);
    entry.total += Number(row.total_isk || 0);
    entry.count += 1;
    const date = parseEveDate_(row.date);
    if (date && (!entry.last || date > entry.last)) entry.last = date;
  });

  const summary = Array.from(byItem.values())
    .sort((a, b) => b.total - a.total)
    .map(entry => [
      entry.item_name,
      entry.category,
      entry.group,
      entry.quantity,
      entry.total,
      entry.quantity ? entry.total / entry.quantity : 0,
      entry.count,
      entry.last || '',
    ]);

  setupModuleSpendSheet_(spend);
  if (summary.length) {
    spend.getRange(4, 1, summary.length, summary[0].length).setValues(summary);
  }
  applyFilter_(spend, 3, 1, Math.max(summary.length + 1, 2), 8);
  spend.autoResizeColumns(1, 8);
  spend.getRange('E:F').setNumberFormat('#,##0.00');
  spend.getRange('H:H').setNumberFormat('yyyy-mm-dd hh:mm');
}

function loadMarketToolTable() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.MARKET_TOOL);
  const itemName = String(sheet.getRange('B2').getValue() || '').trim();
  const regionName = String(sheet.getRange('B3').getValue() || '').trim();
  const orderType = String(sheet.getRange('B4').getValue() || 'sell').trim().toLowerCase();

  if (!itemName || !regionName) {
    SpreadsheetApp.getUi().alert('Enter an item name in B2 and a region name in B3 first.');
    return;
  }

  const typeId = resolveSearchId_(itemName, 'inventory_type');
  const regionId = resolveSearchId_(regionName, 'region');
  sheet.getRange('B5').setValue(typeId);
  sheet.getRange('B6').setValue(regionId);

  const orders = fetchAllMarketOrders_(regionId, typeId, orderType);
  const systemNames = resolveEsiNames_(orders.map(order => order.system_id));
  const locationNames = resolveEsiNames_(orders.map(order => order.location_id));
  const headers = [
    'duration',
    'is_buy_order',
    'issued',
    'location_id',
    'min_volume',
    'order_id',
    'price',
    'range',
    'system_id',
    'type_id',
    'volume_remain',
    'volume_total',
    'reserved',
    'system_name',
    'location_name',
  ];
  const values = orders.map(order => [
    order.duration,
    order.is_buy_order,
    parseEveDate_(order.issued),
    order.location_id,
    order.min_volume,
    order.order_id,
    order.price,
    order.range,
    order.system_id,
    order.type_id,
    order.volume_remain,
    order.volume_total,
    '',
    systemNames.get(String(order.system_id)) || order.system_id,
    locationNames.get(String(order.location_id)) || order.location_id,
  ]);

  sheet.getRange('A13:O').clearContent().clearFormat();
  sheet.getRange(13, 1, 1, headers.length).setValues([headers]);
  if (values.length) {
    sheet.getRange(14, 1, values.length, headers.length).setValues(values);
  }
  styleHeader_(sheet.getRange(13, 1, 1, headers.length));
  applyFilter_(sheet, 13, 1, Math.max(values.length + 1, 2), headers.length);
  sheet.autoResizeColumns(1, headers.length);
  sheet.getRange('C:C').setNumberFormat('yyyy-mm-dd hh:mm');
  sheet.getRange('G:G').setNumberFormat('#,##0.00');
}

function setupWalletImportSheet_(sheet) {
  const alreadySetUp = sheet.getRange('A1').getValue() === 'Wallet Import';
  if (!alreadySetUp) sheet.clear();

  sheet.getRange('A1').setValue('Wallet Import');
  sheet.getRange('A2').setValue('Character name (optional)');
  sheet.getRange('A3').setValue('GESI wallet formula');
  sheet.getRange('A4').setValue('Next step');
  if (!alreadySetUp) sheet.getRange('B2').setValue('');
  sheet.getRange('B3').setValue('The live wallet transaction import starts in A6.');
  sheet.getRange('B4').setValue('Run EVE Wallet > Append Wallet Transactions after GESI loads data below.');
  if (!alreadySetUp || !sheet.getRange('A6').getFormula()) {
    sheet.getRange('A6').setFormula('=IF(LEN($B$2),characters_character_wallet_transactions(,$B$2,TRUE),characters_character_wallet_transactions(,,TRUE))');
  }
  sheet.getRange('A1:B4').setFontWeight('bold');
  sheet.getRange('B4').setBackground('#dbeafe');
  sheet.setFrozenRows(6);
  sheet.autoResizeColumns(1, 12);
}

function setupHeaderSheet_(sheet, headers) {
  if (sheet.getLastRow() === 0 || sheet.getRange(1, 1).getValue() !== headers[0]) {
    sheet.clear();
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  } else {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
  styleHeader_(sheet.getRange(1, 1, 1, headers.length));
  sheet.setFrozenRows(1);
  applyFilter_(sheet, 1, 1, Math.max(sheet.getLastRow(), 2), headers.length);
  sheet.autoResizeColumns(1, headers.length);
}

function setupModuleSpendSheet_(sheet) {
  sheet.clear();
  sheet.getRange('A1').setValue('Module and Ship Spend Summary');
  sheet.getRange('A2').setValue('Built from Purchase Ledger buy rows. Run EVE Wallet > Rebuild Purchase Groups to refresh.');
  const headers = ['item_name', 'category', 'group', 'quantity', 'total_spend', 'avg_unit_price', 'purchase_count', 'last_purchase'];
  sheet.getRange(3, 1, 1, headers.length).setValues([headers]);
  styleHeader_(sheet.getRange(3, 1, 1, headers.length));
  sheet.setFrozenRows(3);
}

function readWalletImportRows_(sheet) {
  const values = sheet.getDataRange().getValues();
  const headerRowIndex = values.findIndex(row => {
    const normalized = row.map(normalizeHeader_);
    return normalized.includes('transaction_id') && normalized.includes('type_id');
  });
  if (headerRowIndex < 0) return [];

  const headers = values[headerRowIndex].map(normalizeHeader_);
  return values.slice(headerRowIndex + 1)
    .filter(row => row.some(cell => cell !== '' && cell !== null))
    .map(row => {
      const obj = {};
      headers.forEach((header, index) => {
        if (header) obj[header] = row[index];
      });
      return obj;
    });
}

function readObjects_(sheet, width) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return [];
  const headers = sheet.getRange(1, 1, 1, width).getValues()[0].map(normalizeHeader_);
  return sheet.getRange(2, 1, lastRow - 1, width).getValues().map((row, index) => {
    const obj = { _sheetRow: index + 2 };
    headers.forEach((header, col) => {
      obj[header] = row[col];
    });
    return obj;
  });
}

function getTypeInfoMap_(typeIds) {
  const ss = SpreadsheetApp.getActive();
  const cacheSheet = ensureSheet_(ss, EVE_SHEET.TYPE_CACHE);
  setupHeaderSheet_(cacheSheet, EVE_HEADERS.TYPE_CACHE);

  const cache = new Map();
  readObjects_(cacheSheet, EVE_HEADERS.TYPE_CACHE.length).forEach(row => {
    if (row.type_id) cache.set(String(row.type_id), row);
  });

  const missing = Array.from(new Set(typeIds.filter(Boolean).map(typeId => String(typeId))))
    .filter(typeId => !cache.has(typeId));
  const rowsToAppend = [];

  missing.forEach(typeId => {
    const type = fetchJson_(`https://esi.evetech.net/latest/universe/types/${encodeURIComponent(typeId)}/?datasource=tranquility`);
    const group = type.group_id
      ? fetchJson_(`https://esi.evetech.net/latest/universe/groups/${encodeURIComponent(type.group_id)}/?datasource=tranquility`)
      : {};
    const category = group.category_id
      ? fetchJson_(`https://esi.evetech.net/latest/universe/categories/${encodeURIComponent(group.category_id)}/?datasource=tranquility`)
      : {};
    const entry = {
      type_id: Number(typeId),
      item_name: type.name || typeId,
      group_id: type.group_id || '',
      group: group.name || '',
      category_id: group.category_id || '',
      category: category.name || '',
      volume: type.volume || '',
      packaged_volume: type.packaged_volume || '',
      last_updated: new Date(),
    };
    cache.set(String(typeId), entry);
    rowsToAppend.push([
      entry.type_id,
      entry.item_name,
      entry.group_id,
      entry.group,
      entry.category_id,
      entry.category,
      entry.volume,
      entry.packaged_volume,
      entry.last_updated,
    ]);
  });

  if (rowsToAppend.length) {
    appendRows_(cacheSheet, rowsToAppend);
    cacheSheet.autoResizeColumns(1, EVE_HEADERS.TYPE_CACHE.length);
  }
  return cache;
}

function resolveEsiNames_(ids) {
  const cleanIds = unique_(ids.filter(Boolean).map(Number).filter(id => !isNaN(id)));
  const result = new Map();
  for (let i = 0; i < cleanIds.length; i += 1000) {
    const chunk = cleanIds.slice(i, i + 1000);
    const response = UrlFetchApp.fetch('https://esi.evetech.net/latest/universe/names/?datasource=tranquility', {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify(chunk),
      muteHttpExceptions: true,
    });
    if (response.getResponseCode() >= 200 && response.getResponseCode() < 300) {
      JSON.parse(response.getContentText()).forEach(item => result.set(String(item.id), item.name));
    }
  }
  return result;
}

function resolveSearchId_(name, category) {
  const url = `https://esi.evetech.net/latest/search/?categories=${encodeURIComponent(category)}&datasource=tranquility&language=en&search=${encodeURIComponent(name)}&strict=true`;
  const json = fetchJson_(url);
  const ids = json[category] || [];
  if (!ids.length) throw new Error(`Could not resolve ${category}: ${name}`);
  return ids[0];
}

function fetchAllMarketOrders_(regionId, typeId, orderType) {
  const typeFilter = orderType === 'buy' || orderType === 'sell' ? orderType : 'all';
  const firstUrl = `https://esi.evetech.net/latest/markets/${regionId}/orders/?datasource=tranquility&order_type=${typeFilter}&page=1&type_id=${typeId}`;
  const firstResponse = UrlFetchApp.fetch(firstUrl, { muteHttpExceptions: true });
  if (firstResponse.getResponseCode() < 200 || firstResponse.getResponseCode() >= 300) {
    throw new Error(`Market order fetch failed: ${firstResponse.getContentText()}`);
  }
  const pages = Number(firstResponse.getHeaders()['X-Pages'] || firstResponse.getHeaders()['x-pages'] || 1);
  let orders = JSON.parse(firstResponse.getContentText());
  for (let page = 2; page <= pages; page += 1) {
    const url = `https://esi.evetech.net/latest/markets/${regionId}/orders/?datasource=tranquility&order_type=${typeFilter}&page=${page}&type_id=${typeId}`;
    orders = orders.concat(fetchJson_(url));
  }
  return orders;
}

function fetchJson_(url) {
  const response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  if (response.getResponseCode() < 200 || response.getResponseCode() >= 300) {
    throw new Error(`ESI request failed (${response.getResponseCode()}): ${url}`);
  }
  return JSON.parse(response.getContentText());
}

function writeLedgerGroupUpdates_(sheet, updates) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2 || updates.size === 0) return;
  const values = sheet.getRange(2, 1, lastRow - 1, EVE_HEADERS.LEDGER.length).getValues();
  values.forEach(row => {
    const update = updates.get(String(row[0]));
    if (!update) return;
    row[17] = update.fit_group_id;
    row[18] = update.fit_group_start;
    row[19] = update.fit_group_end;
    row[20] = update.ship_candidate;
  });
  sheet.getRange(2, 1, values.length, EVE_HEADERS.LEDGER.length).setValues(values);
  formatLedger_(sheet);
}

function writePurchaseGroups_(ss, rows) {
  const sheet = ensureSheet_(ss, EVE_SHEET.PURCHASE_GROUPS);
  sheet.clear();
  sheet.getRange(1, 1, 1, EVE_HEADERS.GROUPS.length).setValues([EVE_HEADERS.GROUPS]);
  styleHeader_(sheet.getRange(1, 1, 1, EVE_HEADERS.GROUPS.length));
  if (rows.length) sheet.getRange(2, 1, rows.length, EVE_HEADERS.GROUPS.length).setValues(rows);
  applyFilter_(sheet, 1, 1, Math.max(rows.length + 1, 2), EVE_HEADERS.GROUPS.length);
  sheet.setFrozenRows(1);
  sheet.autoResizeColumns(1, EVE_HEADERS.GROUPS.length);
  sheet.getRange('B:C').setNumberFormat('yyyy-mm-dd hh:mm');
  sheet.getRange('G:J').setNumberFormat('#,##0.00');
}

function formatLedger_(sheet) {
  setupHeaderSheet_(sheet, EVE_HEADERS.LEDGER);
  const lastRow = Math.max(sheet.getLastRow(), 2);
  sheet.getRange(2, 2, lastRow - 1, 1).setNumberFormat('yyyy-mm-dd hh:mm');
  sheet.getRange(2, 10, lastRow - 1, 3).setNumberFormat('#,##0.00');
  sheet.getRange(2, 19, lastRow - 1, 2).setNumberFormat('yyyy-mm-dd hh:mm');
  sheet.getRange(2, 22, lastRow - 1, 1).setNumberFormat('yyyy-mm-dd hh:mm');
}

function appendRows_(sheet, rows) {
  if (!rows.length) return;
  sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
}

function readColumnValues_(sheet, column, startRow) {
  const lastRow = sheet.getLastRow();
  if (lastRow < startRow) return [];
  return sheet.getRange(startRow, column, lastRow - startRow + 1, 1)
    .getValues()
    .flat()
    .filter(value => value !== '' && value !== null);
}

function applyFilter_(sheet, row, column, numRows, numColumns) {
  const existing = sheet.getFilter();
  if (existing) existing.remove();
  sheet.getRange(row, column, numRows, numColumns).createFilter();
}

function styleHeader_(range) {
  range
    .setFontWeight('bold')
    .setFontColor('#ffffff')
    .setBackground('#1f4e78')
    .setWrap(true);
}

function ensureSheet_(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function normalizeHeader_(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, '_');
}

function truthy_(value) {
  return value === true || String(value).toLowerCase() === 'true' || value === 1 || String(value) === '1';
}

function parseEveDate_(value) {
  if (value instanceof Date) return value;
  if (!value) return null;
  const date = new Date(value);
  return isNaN(date.getTime()) ? null : date;
}

function unique_(values) {
  return Array.from(new Set(values.map(value => String(value)))).map(value => {
    const numeric = Number(value);
    return value !== '' && !isNaN(numeric) ? numeric : value;
  });
}

function sum_(values) {
  return values.reduce((total, value) => total + Number(value || 0), 0);
}

function makeGroupId_(date, index) {
  return Utilities.formatDate(date, 'UTC', "'FG-'yyyyMMdd-HHmm'-'") + String(index).padStart(3, '0');
}
