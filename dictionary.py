dictionary = { #dictionary is mutable we can change it
    "anshuman":21,
    "rohan":22,
    "mohit":20,
    "rahul":23
}
print(dictionary["anshuman"])#access value using key
print(dictionary.get("yash"))#if key is not present it will give none
#print(dictionary(yash))#this will give error because key is not present
#print(dictionary)
#print(type(dictionary))
print(dictionary,type(dictionary))#line 6,7 same output also can write in single line

print(dictionary.items())
print(dictionary.keys())
print(dictionary.values())
dictionary.update({"anshuman":22,"aman":24})#it update the value of anshuman from 21 to 22 
print(dictionary)
