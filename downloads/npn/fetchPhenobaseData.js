const fs = require('fs');
const fastcsv = require('fast-csv');
const axios = require('axios');

// API URL
const apiUrl = 'https://services.usanpn.org/npn_portal/observations/getObservations.json';

// Command-line arguments: start_date, end_date, append
const [start_date, end_date, append] = process.argv.slice(2);

// Validate input parameters
if (!start_date || !end_date || (append !== 'TRUE' && append !== 'FALSE')) {
  console.error('Usage: node fetchObservations.js <start_date> <end_date> <append: TRUE|FALSE>');
  process.exit(1);
}

// Output file path
const outputPath = 'observations.csv';

// Parameters for the API request
const params = {
  start_date,
  end_date,
  request_src: 'custom_script',
};

// Fetch data from the API
async function fetchData() {
  try {
    console.log(`Fetching data from API for dates: ${start_date} to ${end_date}...`);
    const response = await axios.get(apiUrl, { params });

    // Check if the response contains an array of observations
    if (Array.isArray(response.data)) {
      return response.data;
    } else {
      console.error('Invalid response structure:', response.data);
      return null;
    }
  } catch (error) {
    console.error('Error fetching data:', error.message);
    return null;
  }
}

// Extract and format the data as CSV
function formatDataAsCSV(observations) {
  const csvData = observations.map((observation) => ({
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

  return csvData;
}

// Write CSV data to a file (with or without appending)
function writeCSV(csvData, outputPath, append) {
  const writeStream = fs.createWriteStream(outputPath, { flags: append === 'TRUE' ? 'a' : 'w' });

  // If appending, ensure we start on a new line
  if (append === 'TRUE') {
    writeStream.write('\n');
  }

  const csvStream = fastcsv.format({ headers: append !== 'TRUE' });

  csvStream.pipe(writeStream);

  csvData.forEach((row) => {
    csvStream.write(row);
  });

  csvStream.end();

  writeStream.on('finish', () => {
    console.log(`Data successfully ${append === 'TRUE' ? 'appended to' : 'written to'} ${outputPath}`);
  });
}


// Main function
async function main() {
  const observations = await fetchData();
  if (!observations) {
    console.log('No data to process.');
    return;
  }

  const csvData = formatDataAsCSV(observations);
  writeCSV(csvData, outputPath, append);
}

main();

