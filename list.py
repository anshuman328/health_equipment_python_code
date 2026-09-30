list1=["apple","grapes","car","bus","school",28,5,9]
print(list1)
print(list1[0])
list1[0]="fruits"#list are muteable and i replace apple with fruits
print(list1[0])
print(list1[1:4])#slicing from index 1 to index 3
list1.append("coding")#in append we add something at the end of the list
print(list1)

list2=[0,5,6,8,7,1]
list2.sort()
print(list2)
list2.reverse()#here we reverse the sorted list in descending order because before reverse we sorted it in ascending order so its output will be in descending order
print(list2)
list3=[3,4,5,9,8,2]
list3.reverse()#it simply reverse the list string without sorting in ascending or descending order
print(list3) 
list3.insert(2,6969)#in insert we can add something at any index but initially we have to give index then value
print(list3)
list3.pop(5)#in pop we remove element from any index You see the list before and after popping. You don’t see the removed value because you didn’t print it.
#and give output from inserting output [2, 8, 6969, 9, 5, 4, 3]
print(list3)
print("After pop(5):", list3)
#you see removed value because we are printing it
list4=[1,2,3,4,5]#when we use pop function it remove element from given index and also give output of removed element by this code
value=list4.pop(3)#removes and returns element at index 3 not whole list after removing
print(value)
list4.remove(3)# Removes the value 3 from the list
print(list4)
