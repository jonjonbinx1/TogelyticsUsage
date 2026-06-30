import fileinput

for line in fileinput.input('champions_singles copy.csv', inplace=True):
    print(line.lower(), end='')
