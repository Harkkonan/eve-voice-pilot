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
  MARKET_PICKER_DRAFT: 'Market Picker Draft',
  INVENTORY_LEDGER: 'Inventory Ledger',
  TRADE_OPPORTUNITIES: 'Trade Opportunities',
  MISSION_WALLET_IMPORT: 'Mission Wallet Import',
  WALLET_IMPORT: 'Wallet Import',
  PURCHASE_LEDGER: 'Purchase Ledger',
  PURCHASE_GROUPS: 'Purchase Groups',
  MODULE_SPEND: 'Module Spend Summary',
  MISSION_TRACKER: 'Mission Tracker',
  TYPE_CACHE: 'Type Cache',
});

const EVE_HEADERS = Object.freeze({
  TRADE_OPPORTUNITIES: [
    'Observation Date',
    'Item Group',
    'Item',
    'Quantity',
    'Buy Region',
    'Sell Region',
    'Buy Location',
    'Sell Location',
    'Buy Price / Unit',
    'Sell Price / Unit',
    'Expected Refine / Unit',
    'Best Value / Unit',
    'Fees %',
    'Hauling Cost / Unit',
    'Net Profit / Unit',
    'Margin %',
    'ISK/m3',
    'Buy Volume Available',
    'Sell Volume Available',
    'Estimated Days to Sell',
    'Decision',
    'Notes',
  ],
  INVENTORY_LEDGER_SIMPLE: [
    'Date',
    'Item',
    'Quantity',
    'Buy Price / Unit',
    'Source',
    'Current Sell / Unit',
    'Refine Value / Unit',
    'Sold Status',
    'Tax Rate %',
    'Profit',
    'Profit Margin',
    'Notes',
  ],
  MISSION_TRACKER: [
    'Date',
    'Character',
    'Mission Name',
    'Agent',
    'Agent Level',
    'Corp/Faction',
    'Mission Type',
    'System',
    'Security',
    'Ship Used',
    'Fit/Group ID',
    'Start Time',
    'End Time',
    'Duration Minutes',
    'Travel Minutes',
    'Combat Minutes',
    'Salvage/Loot Minutes',
    'Reward ISK',
    'Bonus ISK',
    'Bounties ISK',
    'LP Earned',
    'ISK/LP',
    'LP Value ISK',
    'Loot Value ISK',
    'Salvage Value ISK',
    'Tags/Other ISK',
    'Ammo/Drone Cost',
    'Repair Cost',
    'Ship Loss Cost',
    'Other Cost',
    'Total Cash Income ISK',
    'Total Effective Income ISK',
    'Total Cost ISK',
    'Net Effective ISK',
    'Cash ISK/Hour',
    'Effective ISK/Hour',
    'LP/Hour',
    'Outcome',
    'Difficulty',
    'Risk',
    'Notes',
  ],
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

const MISSION_WALLET_REF_TYPES = Object.freeze([
  'agent_mission_reward',
  'agent_mission_time_bonus_reward',
  'bounty',
  'bounty_prize',
  'bounty_prizes',
  'mission_reward',
]);

const TRADE_ITEM_GROUPS = Object.freeze([
  'Minerals',
  'Materials',
  'Ammunition & Charges',
]);

const TRADE_OPPORTUNITY_HEADER_ROW = 12;

function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu('EVE Market')
    .addItem('Set Up Market Tool', 'setupMarketTool')
    .addItem('Set Up Market Picker Draft', 'setupMarketPickerDraft')
    .addItem('Load Market Tool Table', 'loadMarketToolTable')
    .addItem('Load Market Picker Draft Table', 'loadMarketPickerDraftTable')
    .addSeparator()
    .addItem('Collapse Raw Market Columns', 'collapseMarketRawColumns')
    .addItem('Show Raw Market Columns', 'showMarketRawColumns')
    .addSeparator()
    .addItem('Enable Auto Refresh on Edit', 'installMarketAutoRefresh')
    .addItem('Disable Auto Refresh on Edit', 'removeMarketAutoRefresh')
    .addToUi();

  ui.createMenu('EVE Wallet')
    .addItem('Set Up Wallet Sheets', 'setupWalletSheets')
    .addItem('Append Wallet Transactions', 'appendWalletTransactions')
    .addItem('Rebuild Purchase Groups', 'rebuildPurchaseGroups')
    .addToUi();

  ui.createMenu('EVE Missions')
    .addItem('Set Up Mission Tracker', 'setupMissionTracker')
    .addItem('Set Up Mission Wallet Import', 'setupMissionWalletImport')
    .addItem('Append Mission Wallet Journal', 'appendMissionWalletJournal')
    .addToUi();

  ui.createMenu('EVE Inventory')
    .addItem('Simplify Inventory Ledger', 'setupSimplifiedInventoryLedger')
    .addToUi();

  ui.createMenu('EVE Trade')
    .addItem('Set Up Trade Opportunity Tool', 'setupTradeOpportunityTool')
    .addItem('Append Live Trade Opportunity', 'appendLiveTradeOpportunity')
    .addItem('Remove Rows Without Quantity', 'removeTradeRowsWithoutQuantity')
    .addSeparator()
    .addItem('Add Observation Date / Quantity Columns', 'addTradeObservationDateColumn')
    .addItem('Timestamp Blank Observation Dates', 'timestampTradeOpportunityRows')
    .addToUi();
}

function setupMissionTracker() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.MISSION_TRACKER);
  setupMissionTrackerSheet_(sheet);
  SpreadsheetApp.getUi().alert('Mission Tracker is ready. Enter each mission as one row starting on row 11.');
}

function setupMissionWalletImport() {
  const ss = SpreadsheetApp.getActive();
  setupMissionTrackerSheet_(ensureSheet_(ss, EVE_SHEET.MISSION_TRACKER));
  setupMissionWalletImportSheet_(ensureSheet_(ss, EVE_SHEET.MISSION_WALLET_IMPORT));
  SpreadsheetApp.getUi().alert(
    'Mission Wallet Import is ready. Wait for GESI wallet journal data to load, then run EVE Missions > Append Mission Wallet Journal.'
  );
}

function appendMissionWalletJournal() {
  const ss = SpreadsheetApp.getActive();
  const tracker = ensureSheet_(ss, EVE_SHEET.MISSION_TRACKER);
  const importSheet = ensureSheet_(ss, EVE_SHEET.MISSION_WALLET_IMPORT);
  setupMissionTrackerSheet_(tracker);
  setupMissionWalletImportSheet_(importSheet);

  SpreadsheetApp.flush();
  const imported = readWalletJournalImportRows_(importSheet).filter(isMissionWalletJournalRow_);
  if (!imported.length) {
    SpreadsheetApp.getUi().alert(
      'No mission-related wallet journal rows were found. Check that GESI has loaded wallet journal data and that your character has wallet journal access.'
    );
    return;
  }

  const existingIds = readMissionTrackerImportIds_(tracker);
  const newRows = imported.filter(row => {
    const id = walletJournalId_(row);
    return id && !existingIds.has(id);
  });
  if (!newRows.length) {
    SpreadsheetApp.getUi().alert('No new mission wallet journal rows to append.');
    return;
  }

  const characterName = String(importSheet.getRange('B2').getValue() || tracker.getRange('B5').getValue() || '').trim();
  const appended = appendMissionJournalRows_(tracker, newRows, characterName);
  SpreadsheetApp.getUi().alert(
    `Appended ${appended} mission payout row(s). ESI wallet data tracks rewards/bonuses/bounties, not live mission objectives or progress.`
  );
}

function setupMarketTool() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.MARKET_TOOL);
  setupMarketToolSheet_(sheet);
  SpreadsheetApp.getUi().alert('Market Tool is ready. Change B2/B3/B4, then run EVE Market > Load Market Tool Table.');
}

function setupMarketPickerDraft() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.MARKET_PICKER_DRAFT);
  setupMarketPickerDraftSheet_(sheet);
  SpreadsheetApp.getUi().alert('Market Picker Draft is ready. Type an item in A2 and a region in B2, then run EVE Market > Load Market Picker Draft Table.');
}

function setupSimplifiedInventoryLedger() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.INVENTORY_LEDGER);
  const preservedRows = readInventoryRowsForSimplify_(sheet);
  setupSimplifiedInventoryLedgerSheet_(sheet, preservedRows);
  SpreadsheetApp.getUi().alert('Inventory Ledger is simplified. Existing matching fields were preserved where possible.');
}

function setupTradeOpportunityTool() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.TRADE_OPPORTUNITIES);
  const result = setupTradeOpportunityToolSheet_(sheet);
  SpreadsheetApp.getUi().alert(
    `Trade Opportunity Tool is ready. Preserved ${result.preserved} row(s) with quantity and removed ${result.removed} row(s) without quantity. Enter item/regions/quantity, then run EVE Trade > Append Live Trade Opportunity.`
  );
}

function appendLiveTradeOpportunity() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.TRADE_OPPORTUNITIES);
  setupTradeOpportunityToolSheet_(sheet);

  try {
    const inputs = getTradeOpportunityInputs_(sheet);
    validateTradeOpportunityInputs_(inputs);
    setTradeOpportunityStatus_(sheet, `Loading live market data for ${inputs.itemName}...`);
    SpreadsheetApp.flush();

    const opportunity = buildTradeOpportunityFromMarket_(inputs);
    appendTradeOpportunityRow_(sheet, opportunity);
    setTradeOpportunityStatus_(sheet, `Added live ${inputs.itemName} opportunity at ${formatNow_()}.`);
    SpreadsheetApp.getUi().alert(`Added live trade opportunity for ${opportunity.itemName}.`);
  } catch (error) {
    const message = `Error: ${error.message || error}`;
    setTradeOpportunityStatus_(sheet, message);
    SpreadsheetApp.getUi().alert(message);
  }
}

function removeTradeRowsWithoutQuantity() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.TRADE_OPPORTUNITIES);
  const result = setupTradeOpportunityToolSheet_(sheet);
  SpreadsheetApp.getUi().alert(`Removed ${result.removed} trade opportunity row(s) without quantity.`);
}

function addTradeObservationDateColumn() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.TRADE_OPPORTUNITIES);
  const result = ensureTradeObservationDateColumn_(sheet);
  SpreadsheetApp.getUi().alert(result.message);
}

function timestampTradeOpportunityRows() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.TRADE_OPPORTUNITIES);
  const result = ensureTradeObservationDateColumn_(sheet);
  const count = fillBlankTradeObservationDates_(sheet, result.headerRow, result.observationCol, result.quantityCol);
  SpreadsheetApp.getUi().alert(`Stamped ${count} trade opportunity row(s) with today's date. Rows without Quantity were skipped.`);
}

function loadMarketPickerDraftTable() {
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.MARKET_PICKER_DRAFT);
  if (sheet.getRange('A1').getValue() !== 'Item' || sheet.getRange('B1').getValue() !== 'Region') {
    setupMarketPickerDraftSheet_(sheet, true);
  }
  loadMarketToolTableForSheet_(sheet, true);
}

function installMarketAutoRefresh() {
  const ss = SpreadsheetApp.getActive();
  removeMarketAutoRefresh_();
  ScriptApp.newTrigger('marketToolEditRefresh')
    .forSpreadsheet(ss)
    .onEdit()
    .create();
  setupMarketToolSheet_(ensureSheet_(ss, EVE_SHEET.MARKET_TOOL));
  SpreadsheetApp.getUi().alert('Market auto-refresh is enabled. Editing B2, B3, or B4 will reload the Market Tool table.');
}

function removeMarketAutoRefresh() {
  const removed = removeMarketAutoRefresh_();
  SpreadsheetApp.getUi().alert(
    removed
      ? 'Market auto-refresh was disabled.'
      : 'No Market Tool auto-refresh trigger was found.'
  );
}

function collapseMarketRawColumns() {
  const sheet = getActiveMarketSheet_();
  collapseMarketRawColumns_(sheet);
}

function showMarketRawColumns() {
  const sheet = getActiveMarketSheet_();
  showMarketRawColumns_(sheet);
}

function marketToolEditRefresh(e) {
  if (!e || !e.range) return;
  const range = e.range;
  const sheet = range.getSheet();
  if (![EVE_SHEET.MARKET_TOOL, EVE_SHEET.MARKET_PICKER_DRAFT].includes(sheet.getName())) return;
  if (!isMarketInputEdit_(sheet, range)) return;

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;
  try {
    setMarketStatus_(sheet, 'Inputs changed. Loading market orders...');
    loadMarketToolTableForSheet_(sheet, false);
  } finally {
    lock.releaseLock();
  }
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

function loadMarketToolTable(showAlert) {
  showAlert = showAlert !== false;
  const ss = SpreadsheetApp.getActive();
  const sheet = ensureSheet_(ss, EVE_SHEET.MARKET_TOOL);
  setupMarketToolSheet_(sheet);
  loadMarketToolTableForSheet_(sheet, showAlert);
}

function loadMarketToolTableForSheet_(sheet, showAlert) {
  try {
    const outputRow = getMarketOutputRow_(sheet);
    const inputs = getMarketInputs_(sheet);
    const itemName = inputs.itemName;
    const regionName = inputs.regionName;
    const rawOrderType = inputs.orderType;
    const orderType = ['buy', 'sell', 'all'].includes(rawOrderType) ? rawOrderType : 'sell';
    inputs.orderTypeRange.setValue(
      sheet.getName() === EVE_SHEET.MARKET_PICKER_DRAFT
        ? orderType.charAt(0).toUpperCase() + orderType.slice(1)
        : orderType
    );

    if (!itemName || !regionName) {
      const message = sheet.getName() === EVE_SHEET.MARKET_PICKER_DRAFT
        ? 'Enter an item name in A2 and a region name in B2 first.'
        : 'Enter an item name in B2 and a region name in B3 first.';
      setMarketStatus_(sheet, message);
      if (showAlert) SpreadsheetApp.getUi().alert(message);
      return;
    }

    setMarketStatus_(sheet, `Loading ${orderType} orders for ${itemName} in ${regionName}...`);
    SpreadsheetApp.flush();

    const typeId = resolveSearchId_(itemName, 'inventory_type');
    const regionId = resolveSearchId_(regionName, 'region');
    setResolvedMarketIds_(sheet, typeId, regionId);

    const orders = fetchAllMarketOrders_(regionId, typeId, orderType);
    const systemNames = resolveEsiNames_(orders.map(order => order.system_id));
    const locationNames = resolveEsiNames_(orders.map(order => order.location_id));
    const visibleHeaders = getMarketVisibleHeaders_();
    const rawHeaders = getMarketOrderHeaders_();
    const visibleStartCol = getMarketVisibleStartColumn_(sheet);
    const rawStartCol = getMarketRawStartColumn_(sheet);
    const visibleValues = orders.map(order => [
      systemNames.get(String(order.system_id)) || order.system_id,
      locationNames.get(String(order.location_id)) || order.location_id,
      order.price,
      order.volume_remain,
    ]);
    const rawValues = orders.map(order => [
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

    sheet.getRange(outputRow, 1, Math.max(sheet.getMaxRows() - outputRow + 1, 1), rawStartCol + rawHeaders.length - 1).clearContent().clearFormat();
    sheet.getRange(outputRow, visibleStartCol, 1, visibleHeaders.length).setValues([visibleHeaders]);
    sheet.getRange(outputRow, rawStartCol, 1, rawHeaders.length).setValues([rawHeaders]);
    if (visibleValues.length) {
      sheet.getRange(outputRow + 1, visibleStartCol, visibleValues.length, visibleHeaders.length).setValues(visibleValues);
      sheet.getRange(outputRow + 1, rawStartCol, rawValues.length, rawHeaders.length).setValues(rawValues);
    }
    styleHeader_(sheet.getRange(outputRow, visibleStartCol, 1, visibleHeaders.length));
    styleHeader_(sheet.getRange(outputRow, rawStartCol, 1, rawHeaders.length));
    applyFilter_(sheet, outputRow, visibleStartCol, Math.max(visibleValues.length + 1, 2), rawStartCol + rawHeaders.length - visibleStartCol);
    sheet.autoResizeColumns(1, rawStartCol - 1);
    sheet.autoResizeColumns(rawStartCol, rawHeaders.length);
    formatMarketOutputColumns_(sheet);
    collapseMarketRawColumns_(sheet);
    setMarketStatus_(sheet, `Loaded ${visibleValues.length} ${orderType} order(s) for ${itemName} in ${regionName} at ${formatNow_()}.`);
  } catch (error) {
    const message = `Error: ${error.message || error}`;
    setMarketStatus_(sheet, message);
    if (showAlert) SpreadsheetApp.getUi().alert(message);
  }
}

function setupMarketToolSheet_(sheet) {
  const visibleHeaders = getMarketVisibleHeaders_();
  const rawHeaders = getMarketOrderHeaders_();
  const outputRow = getMarketOutputRow_(sheet);
  const visibleStartCol = getMarketVisibleStartColumn_(sheet);
  const rawStartCol = getMarketRawStartColumn_(sheet);

  sheet.getRange('A1').setValue('Market Tool');
  sheet.getRange('A2').setValue('Item Name');
  sheet.getRange('A3').setValue('Region');
  sheet.getRange('A4').setValue('Order Type');
  sheet.getRange('A5').setValue('Resolved Type ID');
  sheet.getRange('A6').setValue('Resolved Region ID');
  sheet.getRange('A7').setValue('Status');

  if (sheet.getRange('B2').isBlank()) sheet.getRange('B2').setValue('Tritanium');
  if (sheet.getRange('B3').isBlank()) sheet.getRange('B3').setValue('Devoid');
  if (sheet.getRange('B4').isBlank()) sheet.getRange('B4').setValue('sell');
  setDropdown_(sheet.getRange('B4'), ['sell', 'buy', 'all']);
  setMarketStatus_(sheet, 'Ready. Change B2/B3/B4, then run EVE Market > Load Market Tool Table.');

  sheet.getRange('A1:B7')
    .setFontWeight('bold')
    .setWrap(true);
  sheet.getRange('A1:B1').setBackground('#dbeafe');
  sheet.getRange('A7:B7').setBackground('#fef3c7');
  sheet.setFrozenRows(13);

  if (sheet.getRange('A13').getValue() === 'duration') {
    sheet.getRange('A13:U').clearContent().clearFormat();
  }
  if (sheet.getRange(outputRow, visibleStartCol).isBlank()) {
    sheet.getRange(outputRow, visibleStartCol, 1, visibleHeaders.length).setValues([visibleHeaders]);
    sheet.getRange(outputRow, rawStartCol, 1, rawHeaders.length).setValues([rawHeaders]);
    styleHeader_(sheet.getRange(outputRow, visibleStartCol, 1, visibleHeaders.length));
    styleHeader_(sheet.getRange(outputRow, rawStartCol, 1, rawHeaders.length));
    applyFilter_(sheet, outputRow, visibleStartCol, 2, rawStartCol + rawHeaders.length - visibleStartCol);
  }
  sheet.autoResizeColumns(1, rawStartCol - 1);
  sheet.autoResizeColumns(rawStartCol, rawHeaders.length);
  collapseMarketRawColumns_(sheet);
}

function setupMarketPickerDraftSheet_(sheet, preserveInputs) {
  const outputRow = getMarketOutputRow_(sheet);
  const visibleHeaders = getMarketVisibleHeaders_();
  const rawHeaders = getMarketOrderHeaders_();
  const visibleStartCol = getMarketVisibleStartColumn_(sheet);
  const rawStartCol = getMarketRawStartColumn_(sheet);
  const priorItem = sheet.getRange('A2').getValue();
  const priorRegion = sheet.getRange('B2').getValue();
  const priorOrderType = sheet.getRange('C2').getValue();

  sheet.getImages().forEach(image => image.remove());
  sheet.getRange(1, 1, sheet.getMaxRows(), Math.min(sheet.getMaxColumns(), 21)).breakApart();
  sheet.clear();
  if (sheet.getMaxRows() < 80) sheet.insertRowsAfter(sheet.getMaxRows(), 80 - sheet.getMaxRows());
  if (sheet.getMaxColumns() < 21) sheet.insertColumnsAfter(sheet.getMaxColumns(), 21 - sheet.getMaxColumns());

  sheet.getRange('A1:C1').setValues([['Item', 'Region', 'Buy or Sell order']]);
  sheet.getRange('A2:C2').setValues([[
    preserveInputs && priorItem ? priorItem : 'Tritanium',
    preserveInputs && priorRegion ? priorRegion : 'Domain',
    preserveInputs && priorOrderType ? priorOrderType : 'Sell',
  ]]);
  setDropdown_(sheet.getRange('C2'), ['Sell', 'Buy', 'All']);
  setMarketStatus_(sheet, 'Ready. Change A2/B2/C2, then run EVE Market > Load Market Picker Draft Table.');

  sheet.getRange('A1:C1').setFontWeight('bold').setBackground('#dbeafe');
  sheet.getRange('A2:C2').setBackground('#f8fafc');
  sheet.getRange('A3:D3').setBackground('#fef3c7').setWrap(true);
  sheet.getRange(outputRow, visibleStartCol, 1, visibleHeaders.length).setValues([visibleHeaders]);
  sheet.getRange(outputRow, rawStartCol, 1, rawHeaders.length).setValues([rawHeaders]);
  styleHeader_(sheet.getRange(outputRow, visibleStartCol, 1, visibleHeaders.length));
  styleHeader_(sheet.getRange(outputRow, rawStartCol, 1, rawHeaders.length));
  applyFilter_(sheet, outputRow, visibleStartCol, 2, rawStartCol + rawHeaders.length - visibleStartCol);

  sheet.setFrozenRows(outputRow);
  sheet.autoResizeColumns(1, rawStartCol - 1);
  sheet.autoResizeColumns(rawStartCol, rawHeaders.length);
  collapseMarketRawColumns_(sheet);
}

function getMarketOutputRow_(sheet) {
  return sheet.getName() === EVE_SHEET.MARKET_PICKER_DRAFT ? 5 : 13;
}

function getActiveMarketSheet_() {
  const ss = SpreadsheetApp.getActive();
  const active = ss.getActiveSheet();
  if ([EVE_SHEET.MARKET_TOOL, EVE_SHEET.MARKET_PICKER_DRAFT].includes(active.getName())) return active;
  return ensureSheet_(ss, EVE_SHEET.MARKET_TOOL);
}

function getMarketInputs_(sheet) {
  if (sheet.getName() === EVE_SHEET.MARKET_PICKER_DRAFT) {
    return {
      itemName: String(sheet.getRange('A2').getValue() || '').trim(),
      regionName: String(sheet.getRange('B2').getValue() || '').trim(),
      orderType: String(sheet.getRange('C2').getValue() || 'sell').trim().toLowerCase(),
      orderTypeRange: sheet.getRange('C2'),
    };
  }
  return {
    itemName: String(sheet.getRange('B2').getValue() || '').trim(),
    regionName: String(sheet.getRange('B3').getValue() || '').trim(),
    orderType: String(sheet.getRange('B4').getValue() || 'sell').trim().toLowerCase(),
    orderTypeRange: sheet.getRange('B4'),
  };
}

function setResolvedMarketIds_(sheet, typeId, regionId) {
  if (sheet.getName() === EVE_SHEET.MARKET_PICKER_DRAFT) return;
  sheet.getRange('B5').setValue(typeId);
  sheet.getRange('B6').setValue(regionId);
}

function getMarketVisibleStartColumn_(sheet) {
  return sheet.getName() === EVE_SHEET.MARKET_PICKER_DRAFT ? 1 : 3;
}

function getMarketRawStartColumn_(sheet) {
  return getMarketVisibleStartColumn_(sheet) + getMarketVisibleHeaders_().length;
}

function getMarketVisibleHeaders_() {
  return ['Output system name', 'Output Location', 'Output price', 'Output quantity'];
}

function getMarketOrderHeaders_() {
  return [
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
}

function collapseMarketRawColumns_(sheet) {
  const rawStartCol = getMarketRawStartColumn_(sheet);
  sheet.showColumns(1, rawStartCol - 1);
  sheet.hideColumns(rawStartCol, getMarketOrderHeaders_().length);
}

function showMarketRawColumns_(sheet) {
  const rawStartCol = getMarketRawStartColumn_(sheet);
  sheet.showColumns(1, rawStartCol + getMarketOrderHeaders_().length - 1);
}

function setMarketStatus_(sheet, message) {
  if (sheet.getName() === EVE_SHEET.MARKET_PICKER_DRAFT) {
    sheet.getRange('A3:D3')
      .breakApart()
      .mergeAcross()
      .clearContent()
      .setValue(message)
      .setWrap(true);
    return;
  }
  sheet.getRange('B7')
    .clearContent()
    .setValue(message)
    .setWrap(true);
}

function formatMarketOutputColumns_(sheet) {
  const visibleStartCol = getMarketVisibleStartColumn_(sheet);
  const rawStartCol = getMarketRawStartColumn_(sheet);
  const maxRows = sheet.getMaxRows();
  sheet.getRange(1, visibleStartCol + 2, maxRows, 1).setNumberFormat('#,##0.00');
  sheet.getRange(1, visibleStartCol + 3, maxRows, 1).setNumberFormat('#,##0');
  sheet.getRange(1, rawStartCol + 2, maxRows, 1).setNumberFormat('yyyy-mm-dd hh:mm');
  sheet.getRange(1, rawStartCol + 6, maxRows, 1).setNumberFormat('#,##0.00');
}

function isMarketInputEdit_(sheet, range) {
  if (sheet.getName() === EVE_SHEET.MARKET_PICKER_DRAFT) {
    const touchesInputRow = range.getRow() <= 2 && range.getLastRow() >= 2;
    const touchesInputColumns = range.getColumn() <= 3 && range.getLastColumn() >= 1;
    return touchesInputRow && touchesInputColumns;
  }
  const touchesInputRows = range.getRow() <= 4 && range.getLastRow() >= 2;
  const touchesInputColumn = range.getColumn() <= 2 && range.getLastColumn() >= 2;
  return touchesInputRows && touchesInputColumn;
}

function removeMarketAutoRefresh_() {
  let removed = 0;
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === 'marketToolEditRefresh') {
      ScriptApp.deleteTrigger(trigger);
      removed += 1;
    }
  });
  return removed > 0;
}

function formatNow_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm:ss');
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

function setupSimplifiedInventoryLedgerSheet_(sheet, preservedRows) {
  const headers = EVE_HEADERS.INVENTORY_LEDGER_SIMPLE;
  const formulaRows = Math.max(200, preservedRows.length + 50);

  sheet.clear();
  sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  styleHeader_(sheet.getRange(1, 1, 1, headers.length));

  if (preservedRows.length) {
    sheet.getRange(2, 1, preservedRows.length, headers.length).setValues(preservedRows);
  }

  setInventoryLedgerFormulas_(sheet, formulaRows);
  setInventoryLedgerValidation_(sheet, formulaRows);
  formatSimplifiedInventoryLedger_(sheet, formulaRows);
}

function readInventoryRowsForSimplify_(sheet) {
  if (!sheet || sheet.getLastRow() < 2 || sheet.getLastColumn() < 1) return [];

  const lastRow = sheet.getLastRow();
  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(normalizeHeader_);
  if (!headers.some(Boolean)) return [];

  const values = sheet.getRange(2, 1, lastRow - 1, lastCol).getValues();
  return values
    .filter(row => row.some(cell => cell !== '' && cell !== null))
    .map(row => [
      pickInventoryValue_(row, headers, ['date', 'date_bought']),
      pickInventoryValue_(row, headers, ['item']),
      pickInventoryValue_(row, headers, ['quantity', 'qty']),
      pickInventoryValue_(row, headers, ['buy_price_/_unit', 'avg_cost_/_unit', 'avg_cost', 'cost_/_unit']),
      pickInventoryValue_(row, headers, ['source', 'source_/_buy_hub', 'buy_hub', 'location']),
      pickInventoryValue_(row, headers, ['current_sell_/_unit', 'current_sell', 'sell_price_/_unit']),
      pickInventoryValue_(row, headers, ['refine_value_/_unit', 'refine_value']),
      pickInventoryValue_(row, headers, ['sold_status', 'status', 'best_action']),
      pickInventoryValue_(row, headers, ['tax_rate_%', 'taxrate_%', 'tax_rate']),
      '',
      '',
      pickInventoryValue_(row, headers, ['notes', 'plan']),
    ]);
}

function pickInventoryValue_(row, headers, names) {
  for (const name of names) {
    const index = headers.indexOf(name);
    if (index >= 0 && row[index] !== '' && row[index] !== null) return row[index];
  }
  return '';
}

function setInventoryLedgerFormulas_(sheet, formulaRows) {
  sheet.getRange(2, 10, formulaRows, 1)
    .setFormulaR1C1('=IF(OR(RC[-8]="",RC[-7]="",RC[-6]=""),"",IF(RC[-2]="Refined",IF(RC[-3]="","",RC[-7]*RC[-3]*(1-N(RC[-1]))-RC[-7]*RC[-6]),IF(RC[-4]="","",RC[-7]*RC[-4]*(1-N(RC[-1]))-RC[-7]*RC[-6])))');
  sheet.getRange(2, 11, formulaRows, 1)
    .setFormulaR1C1('=IFERROR(RC[-1]/(RC[-8]*RC[-7]),"")');
}

function setInventoryLedgerValidation_(sheet, formulaRows) {
  setDropdown_(sheet.getRange(2, 8, formulaRows, 1), ['Unsold', 'Listed', 'Sold', 'Refined', 'Cancelled']);
  sheet.getRange(2, 9, formulaRows, 1).setNumberFormat('0.00%');
}

function formatSimplifiedInventoryLedger_(sheet, formulaRows) {
  sheet.setFrozenRows(1);
  applyFilter_(sheet, 1, 1, Math.max(formulaRows + 1, 2), EVE_HEADERS.INVENTORY_LEDGER_SIMPLE.length);
  sheet.getRange(2, 1, formulaRows, 1).setNumberFormat('yyyy-mm-dd');
  sheet.getRange(2, 3, formulaRows, 1).setNumberFormat('#,##0.00');
  sheet.getRange(2, 4, formulaRows, 4).setNumberFormat('#,##0.00');
  sheet.getRange(2, 9, formulaRows, 1).setNumberFormat('0.00%');
  sheet.getRange(2, 10, formulaRows, 1).setNumberFormat('#,##0.00');
  sheet.getRange(2, 11, formulaRows, 1).setNumberFormat('0.00%');
  sheet.getRange(1, 1, formulaRows + 1, EVE_HEADERS.INVENTORY_LEDGER_SIMPLE.length).setWrap(true);
  sheet.setColumnWidths(1, 1, 95);
  sheet.setColumnWidths(2, 1, 180);
  sheet.setColumnWidths(3, 1, 90);
  sheet.setColumnWidths(4, 1, 115);
  sheet.setColumnWidths(5, 1, 120);
  sheet.setColumnWidths(6, 2, 125);
  sheet.setColumnWidths(8, 2, 105);
  sheet.setColumnWidths(10, 2, 115);
  sheet.setColumnWidths(12, 1, 180);
}

function setupTradeOpportunityToolSheet_(sheet) {
  const alreadySetUp = sheet.getRange('A1').getValue() === 'Trade Opportunities Tool';
  const importResult = alreadySetUp
    ? { rows: [], removed: 0 }
    : readTradeOpportunityRowsForSetup_(sheet);
  const preservedRows = importResult.rows;

  if (!alreadySetUp) sheet.clear();

  sheet.getRange('A1').setValue('Trade Opportunities Tool');
  sheet.getRange('A2:A9').setValues([
    ['Item Group'],
    ['Item'],
    ['Quantity'],
    ['Buy Region'],
    ['Sell Region'],
    ['Fees %'],
    ['Hauling Cost / Unit'],
    ['Status'],
  ]);
  sheet.getRange('C2').setValue('Examples');
  sheet.getRange('C3').setValue('Minerals: Tritanium, Mexallon, Isogen');
  sheet.getRange('C4').setValue('Materials: Construction Blocks, Coolant, Robotics');
  sheet.getRange('C5').setValue('Ammo/Charges: Scourge Light Missile, Antimatter Charge S, Multifrequency S');

  if (sheet.getRange('B2').isBlank()) sheet.getRange('B2').setValue('Minerals');
  if (sheet.getRange('B3').isBlank()) sheet.getRange('B3').setValue('Tritanium');
  if (sheet.getRange('B4').isBlank()) sheet.getRange('B4').setValue(100000);
  if (sheet.getRange('B5').isBlank()) sheet.getRange('B5').setValue('Domain');
  if (sheet.getRange('B6').isBlank()) sheet.getRange('B6').setValue('Devoid');
  if (sheet.getRange('B7').isBlank()) sheet.getRange('B7').setValue(0.08);
  if (sheet.getRange('B8').isBlank()) sheet.getRange('B8').setValue(0);

  setDropdown_(sheet.getRange('B2'), TRADE_ITEM_GROUPS);
  sheet.getRange('B4').setNumberFormat('#,##0.00');
  sheet.getRange('B7').setNumberFormat('0.00%');
  sheet.getRange('B8').setNumberFormat('#,##0.00');
  setTradeOpportunityStatus_(sheet, 'Ready. Enter item, quantity, buy region, and sell region; then run EVE Trade > Append Live Trade Opportunity.');

  sheet.getRange(TRADE_OPPORTUNITY_HEADER_ROW, 1, 1, EVE_HEADERS.TRADE_OPPORTUNITIES.length)
    .setValues([EVE_HEADERS.TRADE_OPPORTUNITIES]);
  styleHeader_(sheet.getRange(TRADE_OPPORTUNITY_HEADER_ROW, 1, 1, EVE_HEADERS.TRADE_OPPORTUNITIES.length));

  if (preservedRows.length) {
    sheet.getRange(TRADE_OPPORTUNITY_HEADER_ROW + 1, 1, preservedRows.length, EVE_HEADERS.TRADE_OPPORTUNITIES.length)
      .setValues(preservedRows);
  }

  formatTradeOpportunityTool_(sheet);
  const removedFromCurrentTable = removeTradeRowsWithoutQuantity_(sheet);
  formatTradeOpportunityTool_(sheet);
  return {
    preserved: preservedRows.length,
    removed: importResult.removed + removedFromCurrentTable,
  };
}

function getTradeOpportunityInputs_(sheet) {
  return {
    itemGroup: String(sheet.getRange('B2').getValue() || '').trim(),
    itemName: String(sheet.getRange('B3').getValue() || '').trim(),
    quantity: numberValue_(sheet.getRange('B4').getValue()),
    buyRegion: String(sheet.getRange('B5').getValue() || '').trim(),
    sellRegion: String(sheet.getRange('B6').getValue() || '').trim(),
    feesRate: normalizeRate_(sheet.getRange('B7').getValue()),
    haulingCostPerUnit: numberValue_(sheet.getRange('B8').getValue()),
  };
}

function validateTradeOpportunityInputs_(inputs) {
  if (!TRADE_ITEM_GROUPS.includes(inputs.itemGroup)) {
    throw new Error(`Choose one of these item groups: ${TRADE_ITEM_GROUPS.join(', ')}`);
  }
  if (!inputs.itemName) throw new Error('Enter an item name in B3.');
  if (!inputs.quantity || inputs.quantity <= 0) throw new Error('Enter a quantity greater than 0 in B4.');
  if (!inputs.buyRegion) throw new Error('Enter a buy region in B5.');
  if (!inputs.sellRegion) throw new Error('Enter a sell region in B6.');
}

function buildTradeOpportunityFromMarket_(inputs) {
  const typeId = resolveSearchId_(inputs.itemName, 'inventory_type');
  const buyRegionId = resolveSearchId_(inputs.buyRegion, 'region');
  const sellRegionId = resolveSearchId_(inputs.sellRegion, 'region');
  const typeInfo = fetchJson_(`https://esi.evetech.net/latest/universe/types/${encodeURIComponent(typeId)}/?datasource=tranquility`);

  const sourceOrders = fetchAllMarketOrders_(buyRegionId, typeId, 'sell').filter(order => Number(order.volume_remain || 0) > 0);
  const destinationOrders = fetchAllMarketOrders_(sellRegionId, typeId, 'buy').filter(order => Number(order.volume_remain || 0) > 0);
  if (!sourceOrders.length) throw new Error(`No sell orders found for ${inputs.itemName} in ${inputs.buyRegion}.`);
  if (!destinationOrders.length) throw new Error(`No buy orders found for ${inputs.itemName} in ${inputs.sellRegion}.`);

  const buyOrder = sourceOrders.reduce((best, order) => Number(order.price) < Number(best.price) ? order : best, sourceOrders[0]);
  const sellOrder = destinationOrders.reduce((best, order) => Number(order.price) > Number(best.price) ? order : best, destinationOrders[0]);
  const names = resolveEsiNames_([buyOrder.location_id, sellOrder.location_id, buyOrder.system_id, sellOrder.system_id]);
  const buyPrice = numberValue_(buyOrder.price);
  const sellPrice = numberValue_(sellOrder.price);
  const netProfitPerUnit = sellPrice * (1 - inputs.feesRate) - buyPrice - inputs.haulingCostPerUnit;
  const margin = buyPrice ? netProfitPerUnit / buyPrice : '';
  const volume = numberValue_(typeInfo.volume);
  const iskPerM3 = volume ? netProfitPerUnit / volume : '';
  const buyVolume = numberValue_(buyOrder.volume_remain);
  const sellVolume = numberValue_(sellOrder.volume_remain);
  const limitedVolume = Math.min(buyVolume, sellVolume) < inputs.quantity;

  return {
    observedAt: new Date(),
    itemGroup: inputs.itemGroup,
    itemName: typeInfo.name || inputs.itemName,
    quantity: inputs.quantity,
    buyRegion: inputs.buyRegion,
    sellRegion: inputs.sellRegion,
    buyLocation: names.get(String(buyOrder.location_id)) || names.get(String(buyOrder.system_id)) || buyOrder.location_id,
    sellLocation: names.get(String(sellOrder.location_id)) || names.get(String(sellOrder.system_id)) || sellOrder.location_id,
    buyPrice,
    sellPrice,
    bestValue: sellPrice,
    feesRate: inputs.feesRate,
    haulingCostPerUnit: inputs.haulingCostPerUnit,
    netProfitPerUnit,
    margin,
    iskPerM3,
    buyVolume,
    sellVolume,
    decision: getTradeOpportunityDecision_(netProfitPerUnit, limitedVolume),
    notes: [
      'Live ESI market observation',
      `type_id=${typeId}`,
      `buy_region_id=${buyRegionId}`,
      `sell_region_id=${sellRegionId}`,
      `buy_order_id=${buyOrder.order_id}`,
      `sell_order_id=${sellOrder.order_id}`,
      `item_volume=${volume || ''}`,
      limitedVolume ? `requested_quantity_exceeds_available_volume=${Math.min(buyVolume, sellVolume)}` : '',
    ].filter(Boolean).join(' | '),
  };
}

function appendTradeOpportunityRow_(sheet, opportunity) {
  const row = findFirstBlankTradeOpportunityRow_(sheet);
  sheet.getRange(row, 1, 1, EVE_HEADERS.TRADE_OPPORTUNITIES.length).setValues([[
    opportunity.observedAt,
    opportunity.itemGroup,
    opportunity.itemName,
    opportunity.quantity,
    opportunity.buyRegion,
    opportunity.sellRegion,
    opportunity.buyLocation,
    opportunity.sellLocation,
    opportunity.buyPrice,
    opportunity.sellPrice,
    '',
    opportunity.bestValue,
    opportunity.feesRate,
    opportunity.haulingCostPerUnit,
    opportunity.netProfitPerUnit,
    opportunity.margin,
    opportunity.iskPerM3,
    opportunity.buyVolume,
    opportunity.sellVolume,
    '',
    opportunity.decision,
    opportunity.notes,
  ]]);
  formatTradeObservationSheet_(sheet, TRADE_OPPORTUNITY_HEADER_ROW, 1, 4);
}

function findFirstBlankTradeOpportunityRow_(sheet) {
  const startRow = TRADE_OPPORTUNITY_HEADER_ROW + 1;
  const rowCount = Math.max(sheet.getMaxRows() - TRADE_OPPORTUNITY_HEADER_ROW, 1);
  const values = sheet.getRange(startRow, 1, rowCount, EVE_HEADERS.TRADE_OPPORTUNITIES.length).getValues();
  const index = values.findIndex(row => row.every(cell => cell === '' || cell === null));
  if (index >= 0) return startRow + index;

  const oldMaxRows = sheet.getMaxRows();
  sheet.insertRowsAfter(oldMaxRows, 100);
  return oldMaxRows + 1;
}

function getTradeOpportunityDecision_(netProfitPerUnit, limitedVolume) {
  if (netProfitPerUnit <= 0) return 'Skip';
  if (limitedVolume) return 'Limited Volume';
  return 'Review';
}

function setTradeOpportunityStatus_(sheet, message) {
  sheet.getRange('B9').setValue(message);
}

function formatTradeOpportunityTool_(sheet) {
  sheet.getRange('A1:D1').breakApart();
  sheet.getRange('A1:D1').mergeAcross();
  sheet.getRange('A1').setFontWeight('bold').setFontSize(15).setBackground('#dbeafe');
  sheet.getRange('A2:A9').setFontWeight('bold');
  sheet.getRange('C2:C5').setFontWeight('bold').setWrap(true);
  sheet.getRange('B9:D9').setBackground('#fef3c7').setWrap(true);
  sheet.setFrozenRows(TRADE_OPPORTUNITY_HEADER_ROW);
  formatTradeObservationSheet_(sheet, TRADE_OPPORTUNITY_HEADER_ROW, 1, 4);
  sheet.setColumnWidths(1, 1, 135);
  sheet.setColumnWidths(2, 1, 150);
  sheet.setColumnWidths(3, 1, 185);
  sheet.setColumnWidths(4, 3, 110);
  sheet.setColumnWidths(7, 2, 210);
  sheet.setColumnWidths(9, 9, 120);
  sheet.setColumnWidths(18, 3, 110);
  sheet.setColumnWidths(21, 1, 120);
  sheet.setColumnWidths(22, 1, 360);
}

function readTradeOpportunityRowsForSetup_(sheet) {
  const headerInfo = findTradeOpportunityHeader_(sheet);
  if (!headerInfo || sheet.getLastRow() <= headerInfo.row) {
    return { rows: [], removed: 0 };
  }

  const width = Math.max(sheet.getLastColumn(), headerInfo.headers.length);
  const mappedRows = sheet.getRange(headerInfo.row + 1, 1, sheet.getLastRow() - headerInfo.row, width)
    .getValues()
    .filter(row => row.some(cell => cell !== '' && cell !== null))
    .map(row => mapTradeOpportunityRow_(row, headerInfo.headers));
  const rows = mappedRows.filter(row => hasTradeQuantity_(row[3]));
  return {
    rows,
    removed: mappedRows.length - rows.length,
  };
}

function mapTradeOpportunityRow_(row, headers) {
  const item = pickTradeValue_(row, headers, ['item']);
  return [
    pickTradeValue_(row, headers, ['observation_date', 'date']),
    pickTradeValue_(row, headers, ['item_group', 'group', 'category']) || inferTradeItemGroup_(item),
    item,
    pickTradeValue_(row, headers, ['quantity', 'qty', 'amount']),
    pickTradeValue_(row, headers, ['buy_region']),
    pickTradeValue_(row, headers, ['sell_region']),
    pickTradeValue_(row, headers, ['buy_location', 'buy_hub']),
    pickTradeValue_(row, headers, ['sell_location', 'sell/refine_hub', 'sell_hub']),
    pickTradeValue_(row, headers, ['buy_price_/_unit', 'buy_price/unit', 'buy_price']),
    pickTradeValue_(row, headers, ['sell_price_/_unit', 'expected_sell_/_unit', 'expected_sell/unit', 'expected_sell']),
    pickTradeValue_(row, headers, ['expected_refine_/_unit', 'refine_value_/_unit', 'expected_refine']),
    pickTradeValue_(row, headers, ['best_value_/_unit', 'best_value/unit', 'best_value']),
    pickTradeValue_(row, headers, ['fees_%', 'fees', 'taxrate_%']),
    pickTradeValue_(row, headers, ['hauling_cost_/_unit', 'hauling_cost/unit', 'hauling_cost']),
    pickTradeValue_(row, headers, ['net_profit_/_unit', 'net_profit/unit', 'net_profit']),
    pickTradeValue_(row, headers, ['margin_%', 'margin']),
    pickTradeValue_(row, headers, ['isk/m3', 'isk_m3']),
    pickTradeValue_(row, headers, ['buy_volume_available']),
    pickTradeValue_(row, headers, ['sell_volume_available']),
    pickTradeValue_(row, headers, ['estimated_days_to_sell', 'days_to_sell']),
    pickTradeValue_(row, headers, ['decision']),
    pickTradeValue_(row, headers, ['notes']),
  ];
}

function pickTradeValue_(row, headers, names) {
  for (const name of names) {
    const index = headers.indexOf(name);
    if (index >= 0 && row[index] !== '' && row[index] !== null) return row[index];
  }
  return '';
}

function inferTradeItemGroup_(item) {
  const minerals = ['tritanium', 'pyerite', 'mexallon', 'isogen', 'nocxium', 'zydrine', 'megacyte', 'morphite'];
  const lower = String(item || '').trim().toLowerCase();
  return minerals.includes(lower) ? 'Minerals' : '';
}

function removeTradeRowsWithoutQuantity_(sheet) {
  const headerInfo = findTradeOpportunityHeader_(sheet);
  if (!headerInfo || sheet.getLastRow() <= headerInfo.row) return 0;

  const quantityCol = findTradeQuantityColumn_(headerInfo.headers);
  if (!quantityCol) return 0;

  const existingFilter = sheet.getFilter();
  if (existingFilter) existingFilter.remove();

  const width = Math.max(sheet.getLastColumn(), EVE_HEADERS.TRADE_OPPORTUNITIES.length);
  let removed = 0;
  for (let rowNumber = sheet.getLastRow(); rowNumber > headerInfo.row; rowNumber -= 1) {
    const row = sheet.getRange(rowNumber, 1, 1, width).getValues()[0];
    const hasContent = row.some(cell => cell !== '' && cell !== null);
    if (hasContent && !hasTradeQuantity_(row[quantityCol - 1])) {
      sheet.deleteRow(rowNumber);
      removed += 1;
    }
  }
  return removed;
}

function ensureTradeObservationDateColumn_(sheet) {
  if (sheet.getRange('A1').getValue() !== 'Trade Opportunities Tool') {
    const result = setupTradeOpportunityToolSheet_(sheet);
    return {
      headerRow: TRADE_OPPORTUNITY_HEADER_ROW,
      observationCol: 1,
      quantityCol: 4,
      message: `Trade Opportunities is connected to the market pull. Preserved ${result.preserved} row(s) with quantity and removed ${result.removed} row(s) without quantity.`,
    };
  }

  if (sheet.getLastRow() === 0 || sheet.getLastColumn() === 0 || !sheet.getDataRange().getValues().flat().some(Boolean)) {
    setupTradeOpportunityToolSheet_(sheet);
    return {
      headerRow: TRADE_OPPORTUNITY_HEADER_ROW,
      observationCol: 1,
      quantityCol: 4,
      message: 'Trade Opportunities was empty, so I created the live market opportunity tool.',
    };
  }

  let headerInfo = findTradeOpportunityHeader_(sheet);
  if (!headerInfo) {
    setupTradeOpportunityToolSheet_(sheet);
    return {
      headerRow: TRADE_OPPORTUNITY_HEADER_ROW,
      observationCol: 1,
      quantityCol: 4,
      message: 'I could not find the old header row, so I rebuilt the live market opportunity tool.',
    };
  }

  let changed = false;
  let observationCol = headerInfo.headers.indexOf('observation_date') + 1;
  if (!observationCol) {
    const insertCol = headerInfo.firstHeaderCol;
    sheet.insertColumnBefore(insertCol);
    sheet.getRange(headerInfo.row, insertCol).setValue('Observation Date');
    styleHeader_(sheet.getRange(headerInfo.row, insertCol, 1, 1));
    changed = true;
    headerInfo = findTradeOpportunityHeader_(sheet);
    observationCol = headerInfo.headers.indexOf('observation_date') + 1;
  }

  let quantityCol = findTradeQuantityColumn_(headerInfo.headers);
  if (!quantityCol) {
    const itemCol = headerInfo.headers.indexOf('item') + 1;
    const insertAfterCol = itemCol || observationCol;
    sheet.insertColumnAfter(insertAfterCol);
    quantityCol = insertAfterCol + 1;
    sheet.getRange(headerInfo.row, quantityCol).setValue('Quantity');
    styleHeader_(sheet.getRange(headerInfo.row, quantityCol, 1, 1));
    changed = true;
    headerInfo = findTradeOpportunityHeader_(sheet);
    observationCol = headerInfo.headers.indexOf('observation_date') + 1;
    quantityCol = findTradeQuantityColumn_(headerInfo.headers);
  }

  const removed = removeTradeRowsWithoutQuantity_(sheet);
  formatTradeObservationSheet_(sheet, headerInfo.row, observationCol, quantityCol);
  const stamped = fillBlankTradeObservationDates_(sheet, headerInfo.row, observationCol, quantityCol);
  return {
    headerRow: headerInfo.row,
    observationCol,
    quantityCol,
    message: changed
      ? `Added missing trade tracking column(s), removed ${removed} row(s) without quantity, and stamped ${stamped} row(s) with quantity.`
      : `Observation Date and Quantity already exist. Removed ${removed} row(s) without quantity and stamped ${stamped} row(s) with quantity.`,
  };
}

function findTradeOpportunityHeader_(sheet) {
  const maxRows = Math.min(Math.max(sheet.getLastRow(), 1), 20);
  const maxCols = Math.max(sheet.getLastColumn(), 1);
  const values = sheet.getRange(1, 1, maxRows, maxCols).getValues();

  for (let rowIndex = 0; rowIndex < values.length; rowIndex += 1) {
    const normalized = values[rowIndex].map(normalizeHeader_);
    const hasItem = normalized.includes('item');
    const hasDecision = normalized.includes('decision');
    const hasBuy = normalized.some(header => ['buy_price', 'buy_price_/_unit', 'buy_hub'].includes(header));
    const hasMargin = normalized.some(header => ['margin_%', 'margin'].includes(header));
    const hasValue = normalized.some(header => ['expected_net_value', 'expected_sell_/_unit', 'best_value_/_unit', 'isk/m3', 'isk_m3'].includes(header));
    if (hasItem && (hasDecision || hasBuy || hasMargin || hasValue)) {
      const firstHeaderIndex = normalized.findIndex(Boolean);
      return {
        row: rowIndex + 1,
        headers: normalized,
        firstHeaderCol: firstHeaderIndex >= 0 ? firstHeaderIndex + 1 : 1,
      };
    }
  }
  return null;
}

function findTradeQuantityColumn_(headers) {
  const accepted = ['quantity', 'qty', 'amount'];
  const index = headers.findIndex(header => accepted.includes(header));
  return index >= 0 ? index + 1 : 0;
}

function fillBlankTradeObservationDates_(sheet, headerRow, observationCol, quantityCol) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= headerRow || !quantityCol) return 0;

  const rowCount = lastRow - headerRow;
  const dateRange = sheet.getRange(headerRow + 1, observationCol, rowCount, 1);
  const dates = dateRange.getValues();
  const quantities = sheet.getRange(headerRow + 1, quantityCol, rowCount, 1).getValues();
  const tableWidth = Math.max(sheet.getLastColumn(), 1);
  const rows = sheet.getRange(headerRow + 1, 1, rowCount, tableWidth).getValues();
  const today = new Date();
  let stamped = 0;

  rows.forEach((row, index) => {
    const hasContent = row.some(cell => cell !== '' && cell !== null);
    const hasQuantity = hasTradeQuantity_(quantities[index][0]);
    if (hasContent && hasQuantity && !dates[index][0]) {
      dates[index][0] = today;
      stamped += 1;
    }
  });

  if (stamped) dateRange.setValues(dates);
  return stamped;
}

function hasTradeQuantity_(value) {
  if (value === '' || value === null) return false;
  const text = String(value).trim();
  if (!text) return false;
  const numeric = Number(text.replace(/,/g, ''));
  return isNaN(numeric) ? true : numeric > 0;
}

function formatTradeObservationSheet_(sheet, headerRow, observationCol, quantityCol) {
  const lastRow = Math.max(sheet.getLastRow(), headerRow + 1);
  const lastCol = Math.max(sheet.getLastColumn(), EVE_HEADERS.TRADE_OPPORTUNITIES.length);
  const rowCount = Math.max(lastRow - headerRow, 1);
  const headers = sheet.getRange(headerRow, 1, 1, lastCol).getValues()[0].map(normalizeHeader_);
  sheet.setFrozenRows(headerRow);
  sheet.getRange(headerRow, 1, 1, lastCol).setFontWeight('bold').setWrap(true);
  formatTradeColumn_(sheet, headerRow, rowCount, headers, ['observation_date'], 'yyyy-mm-dd hh:mm');
  formatTradeColumn_(sheet, headerRow, rowCount, headers, ['quantity', 'buy_volume_available', 'sell_volume_available'], '#,##0.00');
  formatTradeColumn_(sheet, headerRow, rowCount, headers, [
    'buy_price_/_unit',
    'sell_price_/_unit',
    'expected_refine_/_unit',
    'best_value_/_unit',
    'hauling_cost_/_unit',
    'net_profit_/_unit',
    'isk/m3',
  ], '#,##0.00');
  formatTradeColumn_(sheet, headerRow, rowCount, headers, ['fees_%', 'margin_%'], '0.00%');
  applyFilter_(sheet, headerRow, 1, Math.max(lastRow - headerRow + 1, 2), lastCol);
  sheet.autoResizeColumns(1, Math.min(lastCol, EVE_HEADERS.TRADE_OPPORTUNITIES.length));
}

function formatTradeColumn_(sheet, headerRow, rowCount, headers, names, numberFormat) {
  names.forEach(name => {
    const index = headers.indexOf(name);
    if (index >= 0) sheet.getRange(headerRow + 1, index + 1, rowCount, 1).setNumberFormat(numberFormat);
  });
}

function setupMissionTrackerSheet_(sheet) {
  const headers = EVE_HEADERS.MISSION_TRACKER;
  const startRow = 11;
  const formulaRows = 990;

  sheet.getRange('A1').setValue('Mission Income Tracker');
  sheet.getRange('A2').setValue('Track mission payout, LP, loot, costs, and time so you can compare actual ISK/hour.');
  sheet.getRange('A3').setValue('Default ISK/LP');
  sheet.getRange('A4').setValue('Target Effective ISK/hour');
  sheet.getRange('A5').setValue('Default Character');
  sheet.getRange('A6').setValue('Notes');
  if (sheet.getRange('B3').isBlank()) sheet.getRange('B3').setValue(1000);
  if (sheet.getRange('B4').isBlank()) sheet.getRange('B4').setValue(100000000);

  sheet.getRange('D3').setValue('Total Missions');
  sheet.getRange('D4').setValue('Total Hours');
  sheet.getRange('D5').setValue('Total Cash ISK');
  sheet.getRange('D6').setValue('Total LP');
  sheet.getRange('D7').setValue('Total Net Effective ISK');
  sheet.getRange('E3').setFormula('=COUNTIF(C11:C1000,"<>")');
  sheet.getRange('E4').setFormula('=SUM(N11:N1000)/60');
  sheet.getRange('E5').setFormula('=SUM(AE11:AE1000)');
  sheet.getRange('E6').setFormula('=SUM(U11:U1000)');
  sheet.getRange('E7').setFormula('=SUM(AH11:AH1000)');

  sheet.getRange('G3').setValue('Avg Effective ISK/hour');
  sheet.getRange('G4').setValue('Avg Cash ISK/hour');
  sheet.getRange('G5').setValue('Best Mission');
  sheet.getRange('G6').setValue('Best Mission ISK/hour');
  sheet.getRange('G7').setValue('Avg LP/hour');
  sheet.getRange('H3').setFormula('=IFERROR(SUM(AH11:AH1000)/(SUM(N11:N1000)/60),"")');
  sheet.getRange('H4').setFormula('=IFERROR((SUM(AE11:AE1000)-SUM(AG11:AG1000))/(SUM(N11:N1000)/60),"")');
  sheet.getRange('H5').setFormula('=IFERROR(INDEX(SORT(FILTER({C11:C1000,AJ11:AJ1000},C11:C1000<>"",AJ11:AJ1000<>""),2,FALSE),1,1),"")');
  sheet.getRange('H6').setFormula('=IFERROR(MAX(AJ11:AJ1000),"")');
  sheet.getRange('H7').setFormula('=IFERROR(SUM(U11:U1000)/(SUM(N11:N1000)/60),"")');

  sheet.getRange(10, 1, 1, headers.length).setValues([headers]);
  styleHeader_(sheet.getRange(10, 1, 1, headers.length));
  sheet.setFrozenRows(10);

  setMissionFormulaColumns_(sheet, startRow, formulaRows);
  setMissionDropdowns_(sheet, startRow, formulaRows);
  formatMissionTracker_(sheet, headers.length);
  setMissionConditionalFormats_(sheet);
  applyFilter_(sheet, 10, 1, formulaRows + 1, headers.length);
}

function setMissionFormulaColumns_(sheet, startRow, formulaRows) {
  sheet.getRange(startRow, 14, formulaRows, 1)
    .setFormulaR1C1('=IF(OR(RC[-2]="",RC[-1]=""),"",IF(RC[-1]<RC[-2],RC[-1]+1-RC[-2],RC[-1]-RC[-2])*1440)');
  sheet.getRange(startRow, 23, formulaRows, 1)
    .setFormulaR1C1('=IF(RC[-2]="","",RC[-2]*IF(RC[-1]="",R3C2,RC[-1]))');
  sheet.getRange(startRow, 31, formulaRows, 1)
    .setFormulaR1C1('=IF(COUNTA(RC[-13]:RC[-5])=0,"",SUM(RC[-13]:RC[-11],RC[-7]:RC[-5]))');
  sheet.getRange(startRow, 32, formulaRows, 1)
    .setFormulaR1C1('=IF(RC[-1]="","",RC[-1]+N(RC[-9]))');
  sheet.getRange(startRow, 33, formulaRows, 1)
    .setFormulaR1C1('=IF(COUNTA(RC[-6]:RC[-3])=0,"",SUM(RC[-6]:RC[-3]))');
  sheet.getRange(startRow, 34, formulaRows, 1)
    .setFormulaR1C1('=IF(RC[-2]="","",RC[-2]-N(RC[-1]))');
  sheet.getRange(startRow, 35, formulaRows, 1)
    .setFormulaR1C1('=IF(OR(RC[-21]="",RC[-21]=0,RC[-4]=""),"",(RC[-4]-N(RC[-2]))/(RC[-21]/60))');
  sheet.getRange(startRow, 36, formulaRows, 1)
    .setFormulaR1C1('=IF(OR(RC[-22]="",RC[-22]=0,RC[-2]=""),"",RC[-2]/(RC[-22]/60))');
  sheet.getRange(startRow, 37, formulaRows, 1)
    .setFormulaR1C1('=IF(OR(RC[-23]="",RC[-23]=0,RC[-16]=""),"",RC[-16]/(RC[-23]/60))');
}

function setMissionDropdowns_(sheet, startRow, formulaRows) {
  setDropdown_(sheet.getRange(startRow, 5, formulaRows, 1), ['1', '2', '3', '4', '5']);
  setDropdown_(sheet.getRange(startRow, 7, formulaRows, 1), ['Security', 'Distribution', 'Mining', 'Storyline', 'Epic Arc', 'COSMOS', 'Anomic/Burner', 'Other']);
  setDropdown_(sheet.getRange(startRow, 9, formulaRows, 1), ['Highsec', 'Lowsec', 'Nullsec', 'Wormhole', 'Pochven']);
  setDropdown_(sheet.getRange(startRow, 38, formulaRows, 1), ['Completed', 'Blitzed', 'Declined', 'Failed', 'Abandoned']);
  setDropdown_(sheet.getRange(startRow, 39, formulaRows, 1), ['Easy', 'Normal', 'Hard', 'Very Hard']);
  setDropdown_(sheet.getRange(startRow, 40, formulaRows, 1), ['Low', 'Medium', 'High', 'Ship Lost']);
}

function formatMissionTracker_(sheet, width) {
  sheet.getRange('A1:H2').breakApart();
  sheet.getRange('A1:H1').mergeAcross();
  sheet.getRange('A1').setFontWeight('bold').setFontSize(16).setBackground('#dbeafe');
  sheet.getRange('A2:H2').mergeAcross().setBackground('#f8fafc');
  sheet.getRange('A3:A6').setFontWeight('bold');
  sheet.getRange('D3:D7').setFontWeight('bold');
  sheet.getRange('G3:G7').setFontWeight('bold');
  sheet.getRange('B3:B4').setNumberFormat('#,##0');
  sheet.getRange('E3:E7').setNumberFormat('#,##0.00');
  sheet.getRange('H3:H7').setNumberFormat('#,##0.00');
  sheet.getRange('A11:A1000').setNumberFormat('yyyy-mm-dd');
  sheet.getRange('L11:M1000').setNumberFormat('yyyy-mm-dd hh:mm');
  sheet.getRange('N11:Q1000').setNumberFormat('0.0');
  sheet.getRange('R11:AJ1000').setNumberFormat('#,##0.00');
  sheet.getRange('AK11:AK1000').setNumberFormat('#,##0.00');
  sheet.getRange(10, 1, 991, width).setWrap(true);
  sheet.autoResizeColumns(1, width);
}

function setMissionConditionalFormats_(sheet) {
  const effectiveHourly = sheet.getRange('AJ11:AJ1000');
  const profit = sheet.getRange('AH11:AH1000');
  const rules = [
    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND($AJ11<>"",$AJ11>=$B$4)')
      .setBackground('#dcfce7')
      .setRanges([effectiveHourly])
      .build(),
    SpreadsheetApp.newConditionalFormatRule()
      .whenFormulaSatisfied('=AND($AJ11<>"",$AJ11<$B$4)')
      .setBackground('#fee2e2')
      .setRanges([effectiveHourly])
      .build(),
    SpreadsheetApp.newConditionalFormatRule()
      .whenNumberLessThan(0)
      .setBackground('#fecaca')
      .setRanges([profit])
      .build(),
  ];
  sheet.setConditionalFormatRules(rules);
}

function setupMissionWalletImportSheet_(sheet) {
  const alreadySetUp = sheet.getRange('A1').getValue() === 'Mission Wallet Import';
  if (!alreadySetUp) sheet.clear();

  sheet.getRange('A1').setValue('Mission Wallet Import');
  sheet.getRange('A2').setValue('Character name (optional)');
  sheet.getRange('A3').setValue('GESI wallet journal formula');
  sheet.getRange('A4').setValue('Next step');
  sheet.getRange('A5').setValue('What this can capture');
  if (!alreadySetUp) sheet.getRange('B2').setValue('');
  sheet.getRange('B3').setValue('The live wallet journal import starts in A7.');
  sheet.getRange('B4').setValue('Run EVE Missions > Append Mission Wallet Journal after GESI loads data below.');
  sheet.getRange('B5').setValue('Rewards, time bonuses, and bounties. ESI does not expose live mission objectives, mission title, or exact mission progress.');
  if (!alreadySetUp || !sheet.getRange('A7').getFormula()) {
    sheet.getRange('A7').setFormula('=IF(LEN($B$2),characters_character_wallet_journal($B$2,TRUE),characters_character_wallet_journal(,TRUE))');
  }
  sheet.getRange('A1:B5').setFontWeight('bold').setWrap(true);
  sheet.getRange('B4').setBackground('#dbeafe');
  sheet.getRange('B5').setBackground('#fef3c7');
  sheet.setFrozenRows(7);
  sheet.autoResizeColumns(1, 12);
}

function readWalletJournalImportRows_(sheet) {
  const values = sheet.getDataRange().getValues();
  const headerRowIndex = values.findIndex(row => {
    const normalized = row.map(normalizeHeader_);
    return normalized.includes('id') && normalized.includes('date') && normalized.includes('ref_type');
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

function isMissionWalletJournalRow_(row) {
  const refType = walletJournalRefType_(row);
  if (!walletJournalId_(row) || !row.date) return false;
  if (numberValue_(row.amount) <= 0) return false;
  return MISSION_WALLET_REF_TYPES.includes(refType);
}

function appendMissionJournalRows_(tracker, rows, characterName) {
  let appended = 0;
  rows
    .slice()
    .sort((a, b) => {
      const left = parseEveDate_(a.date);
      const right = parseEveDate_(b.date);
      return (left ? left.getTime() : 0) - (right ? right.getTime() : 0);
    })
    .forEach(row => {
      const buckets = missionJournalAmountBuckets_(row);
      if (!buckets.reward && !buckets.bonus && !buckets.bounty) return;

      const rowNumber = findFirstBlankMissionTrackerRow_(tracker);
      const firstSegment = Array(30).fill('');
      firstSegment[0] = parseEveDate_(row.date) || row.date;
      firstSegment[1] = characterName || '';
      firstSegment[2] = missionJournalName_(row);
      firstSegment[6] = 'Wallet Import';
      firstSegment[17] = buckets.reward;
      firstSegment[18] = buckets.bonus;
      firstSegment[19] = buckets.bounty;

      tracker.getRange(rowNumber, 1, 1, firstSegment.length).setValues([firstSegment]);
      tracker.getRange(rowNumber, 38, 1, 4).setValues([['Imported', '', '', missionJournalNotes_(row)]]);
      setMissionFormulaColumns_(tracker, rowNumber, 1);
      setMissionDropdowns_(tracker, rowNumber, 1);
      appended += 1;
    });
  return appended;
}

function missionJournalAmountBuckets_(row) {
  const refType = walletJournalRefType_(row);
  const amount = numberValue_(row.amount);
  return {
    reward: refType.includes('mission_reward') && !refType.includes('time_bonus') ? amount : '',
    bonus: refType.includes('time_bonus') ? amount : '',
    bounty: refType.includes('bounty') ? amount : '',
  };
}

function missionJournalName_(row) {
  const refType = walletJournalRefType_(row);
  if (refType.includes('time_bonus')) return 'Mission Time Bonus';
  if (refType.includes('mission_reward')) return 'Mission Reward';
  if (refType.includes('bounty')) return 'Bounty Payout';
  return humanizeRefType_(refType);
}

function missionJournalNotes_(row) {
  const pieces = [
    `Wallet Journal ID: ${walletJournalId_(row)}`,
    `Ref Type: ${walletJournalRefType_(row)}`,
  ];
  if (row.description) pieces.push(`Description: ${row.description}`);
  if (row.reason) pieces.push(`Reason: ${row.reason}`);
  if (row.context_id) pieces.push(`Context ID: ${row.context_id}`);
  if (row.context_id_type) pieces.push(`Context Type: ${row.context_id_type}`);
  if (row.first_party_id) pieces.push(`First Party ID: ${row.first_party_id}`);
  if (row.second_party_id) pieces.push(`Second Party ID: ${row.second_party_id}`);
  return pieces.join(' | ');
}

function readMissionTrackerImportIds_(tracker) {
  const startRow = 11;
  const notesCol = 41;
  const rowCount = Math.max(tracker.getMaxRows() - startRow + 1, 1);
  const notes = tracker.getRange(startRow, notesCol, rowCount, 1).getValues().flat();
  const ids = new Set();
  notes.forEach(note => {
    const match = String(note || '').match(/Wallet Journal ID:\s*([^|]+)/i);
    if (match) ids.add(match[1].trim());
  });
  return ids;
}

function findFirstBlankMissionTrackerRow_(sheet) {
  const startRow = 11;
  const width = EVE_HEADERS.MISSION_TRACKER.length;
  const rowCount = Math.max(sheet.getMaxRows() - startRow + 1, 1);
  const values = sheet.getRange(startRow, 1, rowCount, width).getValues();
  const index = values.findIndex(row => !missionTrackerRowHasUserData_(row));
  if (index >= 0) return startRow + index;

  const oldMaxRows = sheet.getMaxRows();
  sheet.insertRowsAfter(oldMaxRows, 100);
  return oldMaxRows + 1;
}

function missionTrackerRowHasUserData_(row) {
  const formulaIndexes = new Set([13, 22, 30, 31, 32, 33, 34, 35, 36]);
  return row.some((cell, index) => {
    return !formulaIndexes.has(index) && cell !== '' && cell !== null;
  });
}

function walletJournalId_(row) {
  return String(row.id || '').trim();
}

function walletJournalRefType_(row) {
  return String(row.ref_type || '').trim().toLowerCase();
}

function humanizeRefType_(value) {
  return String(value || '')
    .split('_')
    .filter(Boolean)
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function normalizeRate_(value) {
  const numeric = numberValue_(value);
  return numeric > 1 ? numeric / 100 : numeric;
}

function numberValue_(value) {
  if (typeof value === 'number') return value;
  const parsed = Number(String(value || '').replace(/,/g, '').trim());
  return isNaN(parsed) ? 0 : parsed;
}

function setDropdown_(range, values) {
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(values, true)
    .setAllowInvalid(true)
    .build();
  range.setDataValidation(rule);
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
  const idsResult = resolveUniverseIdByName_(name, category);
  if (idsResult) return idsResult;

  const strictUrl = `https://esi.evetech.net/latest/search/?categories=${encodeURIComponent(category)}&datasource=tranquility&language=en&search=${encodeURIComponent(name)}&strict=true`;
  const strictJson = fetchJsonOrNull_(strictUrl);
  const strictIds = strictJson ? strictJson[category] || [] : [];
  if (strictIds.length) return strictIds[0];

  const looseUrl = `https://esi.evetech.net/latest/search/?categories=${encodeURIComponent(category)}&datasource=tranquility&language=en&search=${encodeURIComponent(name)}&strict=false`;
  const looseJson = fetchJsonOrNull_(looseUrl);
  const looseIds = looseJson ? looseJson[category] || [] : [];
  if (looseIds.length) return looseIds[0];

  throw new Error(`Could not resolve ${category}: ${name}`);
}

function resolveUniverseIdByName_(name, category) {
  const responseKey = {
    inventory_type: 'inventory_types',
    region: 'regions',
    solar_system: 'systems',
    station: 'stations',
    structure: 'structures',
  }[category];
  if (!responseKey) return null;

  const response = UrlFetchApp.fetch('https://esi.evetech.net/latest/universe/ids/?datasource=tranquility&language=en', {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify([name]),
    muteHttpExceptions: true,
  });
  if (response.getResponseCode() < 200 || response.getResponseCode() >= 300) return null;

  const json = JSON.parse(response.getContentText());
  const matches = json[responseKey] || [];
  const exact = matches.find(match => String(match.name).toLowerCase() === String(name).toLowerCase());
  return exact ? exact.id : matches.length ? matches[0].id : null;
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

function fetchJsonOrNull_(url) {
  const response = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  if (response.getResponseCode() < 200 || response.getResponseCode() >= 300) return null;
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
