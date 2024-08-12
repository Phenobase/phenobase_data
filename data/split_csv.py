import csv
import sys

def split_csv(input_filename, lines_per_file):
    with open(input_filename, 'r', newline='') as input_file:
        reader = csv.reader(input_file)
        headers = next(reader)
        file_count = 1
        output_file = None
        writer = None
        line_count = 0
        
        for row in reader:
            if line_count % lines_per_file == 0:
                if output_file:
                    output_file.close()
                output_filename = f"{input_filename}.out.{file_count}.csv"
                output_file = open(output_filename, 'w', newline='')
                writer = csv.writer(output_file)
                writer.writerow(headers)
                file_count += 1
            writer.writerow(row)
            line_count += 1
        
        if output_file:
            output_file.close()

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python split_csv.py <input_filename> <lines_per_file>")
    else:
        input_filename = sys.argv[1]
        lines_per_file = int(sys.argv[2])
        split_csv(input_filename, lines_per_file)

