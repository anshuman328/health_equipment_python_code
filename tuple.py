tuple=(1, 2, 3, 4, 5, 4)#tuple is immutable we cannot change it
print(tuple)
print(type(tuple))
print(2 in tuple)#it check whether 2 is present in tuple or not and give boolean output
print(6 in tuple)#it check whether 6 is present in tuple or not and give boolean output
value=tuple.count(4)#it count how many times 4 is present in the tuple
print(value)

value2=tuple.index(4)#it give index of given value if it repeated it count only one index which is first occurance
print(value2)

print(len(tuple))
print(max(tuple))
print(min(tuple))
print(sum(tuple))
print(sorted(tuple))#it convert tuple into list and sort it in ascending order 

me=("Anshuman",21,"engineer") #tuple can store different data types 
name,age,profession=me#unpacking of tuple and we have to arrange sequence according to tuple 
print(name) # like name first then age then profession for correct output
print(age)
print(profession)

locations = {("Delhi", "India"): "Hot", ("London", "UK"): "Cold"} #dictionary with tuple as key
print(locations[("Delhi", "India")])  
print(locations[("London", "UK")])


