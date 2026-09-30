name="anshuman"
name2="  anshuman  "
print(name.capitalize())
print(name.endswith("n"))
print(name.startswith("a"))
print(name.lower())
print(name.upper())
print(len(name))
print(name2.strip()) #remove extra spaces from both sides
print(name2.lstrip())#remove space from left side
print(name2.rstrip())#remove space from right side

print(name.replace("ans", "ANS")) 
print(name.find("man")) 

replaced_string = name.replace("anshuman","coding")
print(replaced_string)

id=input("enter name :")
print(f"good morning {id}")#f string use for include variable

name3="python is a good language"
print(name3.find("  "))
name4="python is good   language"
print(name4.find("   "))





