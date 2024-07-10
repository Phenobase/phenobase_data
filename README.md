# GIThub pages view of this page:
[Github Pages View](https://phenobase.github.io/phenobase_data/)

# Loading phenobase data

```
python loader.py sample true
```

# Field Definitions

This table displays the field definitions from the JSON-LD file.

<table id="fields-table" border="1">
  <thead>
    <tr>
      <th>Field</th>
      <th>Datatype</th>
      <th>Source</th>
      <th>Definition</th>
    </tr>
  </thead>
  <tbody>
  </tbody>
</table>

<script src="https://cdnjs.cloudflare.com/ajax/libs/axios/0.21.1/axios.min.js"></script>
<script>
  async function fetchAndDisplayData() {
    try {
      const response = await axios.get('schema.jsonld');
      const jsonData = response.data;

      const tableBody = document.getElementById('fields-table').getElementsByTagName('tbody')[0];

      jsonData.fields.forEach(field => {
        const row = tableBody.insertRow();
        row.insertCell(0).innerText = field.field;
        row.insertCell(1).innerText = field.datatype;
        row.insertCell(2).innerText = field.source;
        row.insertCell(3).innerText = field.definition;
      });
    } catch (error) {
      console.error('Error fetching or parsing the JSON-LD file:', error);
    }
  }

  fetchAndDisplayData();
</script>

