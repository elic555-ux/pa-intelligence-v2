import os

# רשימת תיקיות ושמות שתרצה לסנן החוצה
EXCLUDE_DIRS = {'.git', 'venv', 'env', '__pycache__', 'node_modules', '.idea', '.vscode'}
# אילו סוגי קבצים תרצה לכלול (תוכל להוסיף או לשנות לפי הצורך)
INCLUDE_EXTENSIONS = {'.py', '.js', '.ts', '.html', '.css', '.json', '.sql', '.md'}

output_filename = 'project_bundle.txt'

with open(output_filename, 'w', encoding='utf-8') as outfile:
    for root, dirs, files in os.walk('.'):
        # סינון תיקיות שאינן רצויות
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in INCLUDE_EXTENSIONS:
                file_path = os.path.join(root, file)
                outfile.write(f"\n\n{'='*50}\n")
                outfile.write(f"FILE: {file_path}\n")
                outfile.write(f"{'='*50}\n\n")
                
                try:
                    with open(file_path, 'r', encoding='utf-8') as infile:
                        outfile.write(infile.read())
                except Exception as e:
                    outfile.write(f"[Error reading file: {e}]\n")

print(f"הקובץ נוצר בהצלחה: {output_filename}")
