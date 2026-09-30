name="anshuman"
a= "0123456789"
print(name[0:3])  #slicing first three letters
print(name[-1:-4:-1])#slicing last three letters in reverse order or called negative slicing 
#this we start from -1 index to -4 index with step -1 because in python 
# slicing goes from left to right and to go from right to left we have to give negative step -1 for moving from right to left
print(name[-3:-1]) #slicing last three letters in normal order
print(name[:4])#in this slicing means is [0:4]
print(name[1:])#slicing means is starts from [1:8] length of name (8)
print(a[1:7:2])#in this slicing we are slicing from index 1 to index 7 with skipping element of 2 difference of counting

