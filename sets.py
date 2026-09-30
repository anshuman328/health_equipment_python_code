sets = {1,2,2,3,4}#sets are mutable but it print repeat element only once they are unordered also
print(sets, type(sets))#create set with values
e=set()
print(type(e))#create empty set
sets.add(9)#add value to set
print(sets,type(sets))

sets1=(4,5,6,7,8)
print(sets.union(sets1))#it add two sets and give output witout repeating vlaue ourput {1,2,3,4,5,6,7,8,9}
print(sets.intersection(sets1))#it give common values in both sets

sets.pop()#it remove any random element from set because set is unordered
print(sets)
sets.remove(3)#it remove given element from set
print(sets) 

