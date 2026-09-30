'''a=int(input("enter the age of condidate:"))

if (a>18):
    print("you are eligible for video")

elif (a==18):
    print("you can watch video on your own risk")


elif (a<0):
    print("not valid age")    

else:
    print("go and watch cartoons")
'''
#program of gratest number among three numbers
'''a=int (input("enter number A :"))
b=int (input("enter number B :"))
c=int (input("enter number C :"))
d=int(input("enter number D :"))
if(a>b) and (a>c) and (a>d):
    print("A is greatest number")

elif(b>a) and (b>c) and (b>d):
    print("B is greatest number")

elif(c>a) and (c>b) and (c>d):
    print("C is greatest number") 

else:
    print("D is greatest number")
'''
#program to check number
'''marks1= int(input("enter marks of subject 1:"))
marks2= int(input("enter marks of subect 2:"))
marks3= int(input("enter marks of subject 3:"))

total= marks1+marks2+marks3
percentage= (total/300)*100

if (percentage>=40 and marks1>=33 and marks2>=33 and marks3>=33):
  print("you are pass with", percentage)
else:
    print("you are fail with", percentage)
    '''
#program of checking spam message 
'''
s1="make a lot of money"
s2="buy now"
s3="subscribe this"
s4="click this"

message=input("enter the message:")

if((s1 in message)) or ((s2 in message )) and ((s3 in message)) and ((s4 in message )):
    
        print("this is spam message")
# with or the program detects spam if any suspicious word is present,
# while with and it only detects spam if all suspicious words are present in the same sentence.

else:
    print("this is not spam message")
'''
    

    






    

