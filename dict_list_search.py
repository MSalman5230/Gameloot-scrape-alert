import uuid
from time import time

set1=set()
list1=[]


for i in range(10000000):
    id=str(uuid.uuid4())
    #print(type(id))
    set1.add(id)
    list1.append(id)
    

print(f"Lenght of {len(list1)}")


counter=1
print("set")
start_time=time()
for i in range(len(list1)):
    if list1[i] in set1:
        counter+=1
print(f"Accuracy for set {counter/len(list1)}")
print(f"Time for set {time()-start_time}")




counter=1
print("list")
start_time=time()
for i in range(len(list1)):
    if list1[i] in list1:
        counter+=1
print(f"Accuracy for list {counter/len(list1)}")
print(f"Time for list {time()-start_time}")
