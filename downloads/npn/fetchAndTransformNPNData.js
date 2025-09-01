// File: fetchObservations.js
// Usage:
//   node fetchObservations.js <start_date> <end_date> [mappings_csv_path]
// Example:
//   node fetchObservations.js 2025-08-01 2025-08-02 ./mappings.csv

const fs = require('fs');
const fastcsv = require('fast-csv');
const { parse } = require('fast-csv');
const axios = require('axios');
const { format, addMonths, parseISO, isAfter } = require('date-fns');

// -------------------- Config --------------------
const apiUrl = 'https://services.usanpn.org/npn_portal/observations/getObservations.json?additional_field=dataset_id&additional_field=family';

// Command-line arguments: start_date, end_date, [mappings_path]
const [start_date, end_date, mappingsArg] = process.argv.slice(2);

// Validate input parameters
if (!start_date || !end_date) {
  console.error('Usage: node fetchObservations.js <start_date> <end_date> [mappings_csv_path]');
  process.exit(1);
}

// Mappings file path (default to uploaded file location)
const MAPPINGS_PATH = mappingsArg || '/mnt/data/mappings.csv';

// Output file path with dynamic name
const outputPath = `npn_observations_${start_date}_to_${end_date}.csv`;

// -------------------- Helpers --------------------
const norm = (s) => String(s ?? '').trim();

// Normalize keys for joining mapping <-> NPN description robustly
function normalizeKey(s) {
  return String(s ?? '')
    .replace(/\u00A0/g, ' ')   // non-breaking space -> space
    .replace(/\s+/g, ' ')      // collapse internal whitespace
    .trim()
    .toLowerCase();            // case-insensitive match
}

function getDateChunks(startDate, endDate) {
  const chunks = [];
  let currentStartDate = parseISO(startDate);

  while (isAfter(parseISO(endDate), currentStartDate)) {
    const currentEndDate = addMonths(currentStartDate, 1);
    const chunkEndDate = isAfter(currentEndDate, parseISO(endDate))
      ? endDate
      : format(currentEndDate, 'yyyy-MM-dd');

    chunks.push({ startDate: format(currentStartDate, 'yyyy-MM-dd'), endDate: chunkEndDate });
    currentStartDate = currentEndDate;
  }
  return chunks;
}

async function fetchData(startDate, endDate) {
  const params = { start_date: startDate, end_date: endDate, request_src: 'custom_script' };
  try {
    console.log(`Fetching data from API for dates: ${startDate} to ${endDate}...`);
    const response = await axios.get(apiUrl, { params });
    return Array.isArray(response.data) ? response.data : [];
  } catch (error) {
    console.error(`Error fetching data for dates: ${startDate} to ${endDate}:`, error.message);
    return [];
  }
}

// -------------------- Status parsing --------------------
function parseStatus(raw) {
  // Normalize various Unicode minus signs to ASCII hyphen
  const s = String(raw ?? '')
    .replace(/[\u2212\u2012\u2013\u2014\u2015]/g, '-') // −, ‒, –, —, ―
    .trim()
    .toLowerCase();

  if (s === '') return null; // do NOT coerce blank to 0

  // Optional textual synonyms
  if (s === '-1' || s === '-1.0' || s === 'drop' || s === 'ignore' || s === 'omit') return -1;
  if (s === '0'  || s === '0.0'  || s === 'absent' || s === 'no')                       return 0;
  if (s === '1'  || s === '1.0'  || s === 'present' || s === 'yes' || s === 'observed') return 1;

  const n = Number(s);
  return (n === -1 || n === 0 || n === 1) ? n : null; // invalid -> null
}

// -------------------- Mappings --------------------
/**
 * Build: index[normalized_verbatim_trait][status] = trait
 * Also sets index[key].__DROP__ = true if any row for that key has status -1
 */
function loadMappings(csvPath) {
  return new Promise((resolve, reject) => {
    const index = Object.create(null);
    let rows = 0;
    const byStatus = { '-1': 0, '0': 0, '1': 0 };
    let invalidStatus = 0;

    if (!fs.existsSync(csvPath)) {
      console.warn(`⚠️  Mappings file not found at ${csvPath}. Proceeding with empty mappings.`);
      return resolve({ index, counts: { rows: 0, uniqueDescriptions: 0 } });
    }

    fs.createReadStream(csvPath)
      .pipe(parse({ headers: true, ignoreEmpty: true, trim: true }))
      .on('error', reject)
      .on('data', (raw) => {
        rows++;
        const key = normalizeKey(raw.verbatim_trait);
        const statusNum = parseStatus(raw.status);
        const trait = norm(raw.trait); // may be empty; handled downstream

        if (!key) return;

        if (statusNum === null) {
          invalidStatus++;
          return; // skip invalid/blank statuses entirely
        }

        if (!index[key]) index[key] = Object.create(null);

        if (statusNum === -1) {
          index[key].__DROP__ = true;    // explicit drop flag (unconditional for this key)
        }
        index[key][statusNum] = trait;    // overwrite duplicates on same (key,status)

        byStatus[String(statusNum)]++;
      })
      .on('end', () => {
        const uniqueDescriptions = Object.keys(index).length;
        console.log(
          `Loaded ${rows} mapping row(s) across ${uniqueDescriptions} verbatim_trait value(s) from ${csvPath}. ` +
          `Counts by status: -1=${byStatus['-1']}, 0=${byStatus['0']}, 1=${byStatus['1']}; invalid/blank=${invalidStatus}`
        );
        resolve({ index, counts: { rows, uniqueDescriptions } });
      });
  });
}

/**
 * Decide mapping result for a given normalized description and observed status (0/1).
 *
 * Rules:
 * - If table.__DROP__ is true or (-1 in table): DROP ("minus1")
 * - Else if exact status (0/1) exists AND trait is non-empty -> use it
 * - Else try fallbacks (prefer 1 then 0) with non-empty trait
 * - If mappings exist but traits are empty -> DROP ("empty")
 * - If no mapping rows exist -> DROP ("no-map")
 *
 * Returns one of:
 *   { trait: '...' }
 *   { drop: 'minus1' | 'empty' | 'no-map' }
 */
function resolveTraitMapping(mappingIndex, normalizedKey, observedStatus) {
  const table = mappingIndex[normalizedKey];
  if (!table) return { drop: 'no-map' };

  // Drop wins unconditionally (if any -1 row exists for this description)
  if (table.__DROP__ || (-1 in table)) return { drop: 'minus1' };

  // Exact match (only if non-empty)
  const exact = table[observedStatus];
  if (typeof exact !== 'undefined') {
    if (exact) return { trait: exact };
    return { drop: 'empty' };
  }

  // Fallbacks: prefer 1 then 0, only if non-empty
  const fb1 = table[1];
  if (typeof fb1 !== 'undefined' && fb1) return { trait: fb1 };

  const fb0 = table[0];
  if (typeof fb0 !== 'undefined' && fb0) return { trait: fb0 };

  // Mappings existed but traits empty
  if (typeof fb1 !== 'undefined' || typeof fb0 !== 'undefined') return { drop: 'empty' };

  return { drop: 'no-map' };
}

// -------------------- CSV writing --------------------
function writeCSVIncrementally(csvData, outputPath, isFirstChunk) {
  const writeStream = fs.createWriteStream(outputPath, { flags: isFirstChunk ? 'w' : 'a' });
  const csvStream = fastcsv.format({ headers: isFirstChunk });

  csvStream.pipe(writeStream);
  for (const row of csvData) csvStream.write(row);
  csvStream.end();

  writeStream.on('finish', () => {
    console.log(`Chunk successfully written to ${outputPath}`);
  });
}

// // Fetch the species catalog once, then let us do fast lookups:
// - byId:    species_id -> { family, genus, species, ... }
// - byGS:    "genus|species" (lowercased) -> same object
async function fetchSpeciesCatalog() {
  const url = 'https://services.usanpn.org/npn_portal/species/getSpecies.json';
  try {
    const { data } = await axios.get(url, { params: { request_src: 'custom_script' } });

    const byId = new Map();
    const byGS = new Map();

    if (Array.isArray(data)) {
      for (const s of data) {
        const id = Number(s.species_id);
        const genus = String(s.genus || '').trim();
        const species = String(s.species || '').trim();
        const keyGS = `${genus}|${species}`.toLowerCase();

        const rec = {
          species_id: id,
          family: s.family || s.family_name || '',
          genus,
          species,
        };

        if (!Number.isNaN(id)) byId.set(id, rec);
        if (genus && species) byGS.set(keyGS, rec);
      }
    }

    console.log(`Loaded species catalog: ${byId.size} by id, ${byGS.size} by genus/species.`);
    return { byId, byGS };
  } catch (err) {
    console.error('Failed to load species catalog:', err.message);
    return { byId: new Map(), byGS: new Map() };
  }
}

// -------------------- Main --------------------
async function main() {
  console.log(`Output file: ${outputPath}`);
  const speciesCatalog = await fetchSpeciesCatalog();

 
  // Load mappings once
  const { index: mappingIndex } = await loadMappings(MAPPINGS_PATH);

  // Split requested range into monthly chunks
  const dateChunks = getDateChunks(start_date, end_date);
  let isFirstChunk = true;

  // Counters
let keptCount = 0;
let droppedByMinusOne = 0;
let droppedEmptyTrait = 0;
let droppedNoMap = 0;
let droppedBadObsStatus = 0; // NEW

  for (const chunk of dateChunks) {
    const observations = await fetchData(chunk.startDate, chunk.endDate);

    if (observations.length === 0) {
      console.log(`No data found for dates: ${chunk.startDate} to ${chunk.endDate}`);
      continue;
    }

    const transformed = [];

for (const o of observations) {
  //console.log(o)
  const cleanedDescription = norm(o.phenophase_description);
  const key = normalizeKey(cleanedDescription);

  // 1) Validate observation status from API: must be exactly 0 or 1
  const raw = Number(o.phenophase_status);
  if (raw !== 0 && raw !== 1) {
    // add this counter in your declarations: let droppedBadObsStatus = 0;
    droppedBadObsStatus++;
    continue;
  }
  const obsStatus = raw; // 0 or 1 only

  // 2) Resolve mapping AFTER we know obsStatus is valid
  const decision = resolveTraitMapping(mappingIndex, key, obsStatus);

  // 3) Apply drop rules from mapping
  if (decision?.drop) {
    if (decision.drop === 'minus1')      droppedByMinusOne++;
    else if (decision.drop === 'empty')  droppedEmptyTrait++;
    else                                 droppedNoMap++;      // 'no-map'
    continue;
  }

  // 4) Safety: no trait -> treat as no-map
  if (!decision?.trait) {
    droppedNoMap++;
    continue;
  }

  obsStatus === 1 ? 'Observed' : 'Not Observed';
  let verbatimTrait = cleanedDescription + " ("+obsStatus +")"
  let scientificName = o.genus + " " + o.species;
  
  // // Try by species_id first, then fall back to genus+species
const spById = speciesCatalog.byId.get(Number(o.species_id));
const gsKey = `${String(o.genus || '').trim()}|${String(o.species || '').trim()}`.toLowerCase();
const spByGS = speciesCatalog.byGS.get(gsKey);
const family = (spById?.family || spByGS?.family || '').trim();
  // 5) Keep
  transformed.push({
    dataSource: "National Phenology Network",
    scientificName: scientificName,
    taxonRank: "species",
    basisOfRecord: "Human Observation",
    family,
    genus: o.genus,
    species: o.species,
    annotationID: o.observation_id,
    date: o.observation_date,
    year: new Date(o.observation_date).getFullYear(),
    dataset_id: o.dataset_id,
    dayOfYear: o.day_of_year,
    latitude: o.latitude,
    longitude: o.longitude,
    verbatimTrait: verbatimTrait,
    trait: decision.trait
  });
  keptCount++;
}

    if (transformed.length > 0) {
      writeCSVIncrementally(transformed, outputPath, isFirstChunk);
      isFirstChunk = false;
    } else {
      console.log(`All rows for ${chunk.startDate} to ${chunk.endDate} were dropped by mapping rules.`);
    }
  }

  console.log('Data fetching and writing complete.');
console.log(
  `Kept rows: ${keptCount} | Dropped (-1 map): ${droppedByMinusOne} | Dropped (empty trait): ${droppedEmptyTrait} | Dropped (no map): ${droppedNoMap} | Dropped (bad obs status): ${droppedBadObsStatus}`
);

}

main().catch((err) => {
  console.error('Unhandled error:', err);
  process.exit(1);
});

