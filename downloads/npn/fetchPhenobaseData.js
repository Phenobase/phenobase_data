const fs = require('fs');
const fastcsv = require('fast-csv');
const axios = require('axios');
const { format, addMonths, parseISO, isAfter } = require('date-fns');

// API URL
const apiUrl = 'https://services.usanpn.org/npn_portal/observations/getObservations.json';

// Command-line arguments: start_date, end_date
const [start_date, end_date] = process.argv.slice(2);

// Validate input parameters
if (!start_date || !end_date) {
  console.error('Usage: node fetchObservations.js <start_date> <end_date>');
  process.exit(1);
}

// Output file path
const outputPath = 'observations.csv';

// Function to split the date range into one-month chunks
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

// Fetch data from the API for a given date range
async function fetchData(startDate, endDate) {
  const params = {
    start_date: startDate,
    end_date: endDate,
    request_src: 'custom_script',
  };

  try {
    console.log(`Fetching data from API for dates: ${startDate} to ${endDate}...`);
    const response = await axios.get(apiUrl, { params });

    if (Array.isArray(response.data)) {
      return response.data;
    } else {
      console.error('Invalid response structure:', response.data);
      return [];
    }
  } catch (error) {
    console.error(`Error fetching data for dates: ${startDate} to ${endDate}:`, error.message);
    return [];
  }
}

// Extract and format the data as CSV
function formatDataAsCSV(observations) {
  return observations.map((observation) => ({
    genus: observation.genus,
    species: observation.species,
    observation_id: observation.observation_id,
    observation_date: observation.observation_date,
    year: new Date(observation.observation_date).getFullYear(),
    dataset_id: 'Unknown', // Replace with actual dataset ID if available
    day_of_year: observation.day_of_year,
    latitude: observation.latitude,
    longitude: observation.longitude,
    phenophase_description: observation.phenophase_description,
    phenophase_status: observation.phenophase_status === 1 ? 'Observed' : 'Not Observed',
  }));
}

// Write CSV data incrementally to a file
function writeCSVIncrementally(csvData, outputPath, isFirstChunk) {
  const writeStream = fs.createWriteStream(outputPath, { flags: isFirstChunk ? 'w' : 'a' });
  const csvStream = fastcsv.format({ headers: isFirstChunk });

  csvStream.pipe(writeStream);

  csvData.forEach((row) => {
    csvStream.write(row);
  });

  csvStream.end();

  writeStream.on('finish', () => {
    console.log(`Chunk successfully written to ${outputPath}`);
  });
}

// Main function
async function main() {
  const dateChunks = getDateChunks(start_date, end_date);
  let isFirstChunk = true;

  for (const chunk of dateChunks) {
    const observations = await fetchData(chunk.startDate, chunk.endDate);
    if (observations.length > 0) {
      const csvData = formatDataAsCSV(observations);
      writeCSVIncrementally(csvData, outputPath, isFirstChunk);
      isFirstChunk = false; // Only write the header for the first chunk
    } else {
      console.log(`No data found for dates: ${chunk.startDate} to ${chunk.endDate}`);
    }
  }

  console.log('Data fetching and writing complete.');
}

main();

