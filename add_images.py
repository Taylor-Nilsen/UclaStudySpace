import json
import re

# Image URLs mapping
image_data = """
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-2444.jpeg?itok=DjOsY_Uc	Boelter 2444
https://dts.ucla.edu/sites/default/files/styles/media_library/public/media/images/boelter-2760.png?itok=Mn3qB6c1	Boelter 2760
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-3400.jpeg?itok=yqE70M8t	Boelter 3400
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-4283.jpeg?itok=_8x4RO-C	Boelter 4283
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-4413.jpeg?itok=K0UbVvHh	Boelter 4413
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5249.jpeg?itok=z7O4gAJb	Boelter 5249
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5252.jpeg?itok=987HXADw	Boelter 5252
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5264.jpeg?itok=LGoaiVC6	Boelter 5264
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5272.jpeg?itok=rIR9T4Hb	Boelter 5272
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5273.jpeg?itok=gKwM5SLO	Boelter 5273
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5280.jpeg?itok=Igyerxg4	Boelter 5280
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5419.jpeg?itok=Gp1DDnUD	Boelter 5419
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5420.jpeg?itok=xWaPBQmi	Boelter 5420
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5422.jpeg?itok=0tTf5BIL	Boelter 5422
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5436.jpeg?itok=gghyQ-4v	Boelter 5436
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5440.jpeg?itok=FIxq3TmY	Boelter 5440
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-5514.jpeg?itok=uPb4RYRP	Boelter 5514
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Boelter-9436.jpeg?itok=ACfrbCI2	Boelter 9436
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Broad-2100A.jpeg?itok=U2lsdGui	Broad 2100A
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Broad-2160E.jpeg?itok=IBU11m6W	Broad 2160E
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-1209B.jpeg?itok=n942sdKN	Bunche 1209B
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-1221A.jpeg?itok=TPpCk8Pj	Bunche 1221A
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-1265.jpeg?itok=i8Z-i1zS	Bunche 1265
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2121.jpeg?itok=3Ss5NRYL	Bunche 2121
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2150.jpeg?itok=-_YCTFly	Bunche 2150
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2156.jpeg?itok=6IttL292	Bunche 2156
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2160.jpeg?itok=rtl_qDNH	Bunche 2160
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2168.jpeg?itok=qnRmSu4U	Bunche 2168
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2173.jpeg?itok=ezsKANh5	Bunche 2173
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2174.jpeg?itok=MA5f5aBj	Bunche 2174
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2178.jpeg?itok=AN75-POG	Bunche 2178
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2181.jpeg?itok=BNqeAmQ2	Bunche 2181
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-2209A.jpeg?itok=nqrKU4S9	Bunche 2209A
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3117.jpeg?itok=87c8cFRc	Bunche 3117
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3123.jpeg?itok=v_DMgri4	Bunche 3123
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3143.jpeg?itok=YPuGeXIQ	Bunche 3143
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3150.jpeg?itok=pfChudNn	Bunche 3150
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3153.jpeg?itok=n-_nGe4U	Bunche 3153
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3156.jpeg?itok=n2JpO5ez	Bunche 3156
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3157.jpeg?itok=_7euGxJo	Bunche 3157
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3164.jpeg?itok=YvVQGmXG	Bunche 3164
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3170.jpeg?itok=6O5u5r3y	Bunche 3170
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3178.jpeg?itok=2sA9N9EA	Bunche 3178
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-3211.jpeg?itok=XqOAuAhI	Bunche 3211
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Bunche-A152.jpeg?itok=UGZBM-jp	Bunche A152
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-121.jpeg?itok=Eglwx4fj	Dodd 121
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-146.jpeg?itok=9OQ83DIS	Dodd 146
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-147.jpeg?itok=2Kea4Pss	Dodd 147
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-154.jpeg?itok=jzRK7N4p	Dodd 154
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-161.jpeg?itok=zvl5Ssqc	Dodd 161
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-162.jpeg?itok=VLdgXOGk	Dodd 162
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-167.jpeg?itok=sfu2iC3o	Dodd 167
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-170.jpeg?itok=P-4Dvx98	Dodd 170
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-175.jpeg?itok=0HtpAqkB	Dodd 175
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-178.jpeg?itok=LH2kLBKZ	Dodd 178
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Dodd-78.jpeg?itok=TrufnHl0	Dodd 78
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Fowler-A103B.jpeg?itok=hWWd1Hew	Fowler A103B
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Fowler-A139.jpeg?itok=dEHlSlc_	Fowler A139
https://dts.ucla.edu/sites/default/files/styles/media_library/public/media/images/FRANZ_1178-Full_Room-320-214.jpg?itok=hBUL89y_	Franz 1178
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Franz-1260.jpeg?itok=eAUUUR4t	Franz 1260
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Franz-2258A.jpeg?itok=Af5BShA-	Franz 2258A
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Franz-2288.jpeg?itok=P1QLt0cP	Franz 2288
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Geology-3656.jpeg?itok=BeNNzUpj	Geology 3656
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Geology-4645.jpeg?itok=78HeU1U3	Geology 4645
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Geology-4660.jpeg?itok=qyLmlWTk	Geology 4660
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Geology-6704.jpeg?itok=QV_oWPKD	Geology 6704
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-110.jpeg?itok=Lf4il18n	Haines 110
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-118.jpeg?itok=dvSruyc-	Haines 118
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-122.jpeg?itok=EAL3g-Mo	Haines 122
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-220.jpeg?itok=2Bo4OPYd	Haines 220
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-39.jpeg?itok=i2CDVKzU	Haines 39
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A18.jpeg?itok=sURUlB5g	Haines A18
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A2.jpeg?itok=AfVk3D1R	Haines A2
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A20.jpeg?itok=bQr1jFaI	Haines A20
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A24.jpeg?itok=qLo1Hkn6	Haines A24
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A25.jpeg?itok=AwnpMtAf	Haines A25
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A28.jpeg?itok=GA2u-H_i	Haines A28
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A44.jpeg?itok=x0eOiw5v	Haines A44
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A6.jpeg?itok=YhN8Gpt9	Haines A6
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A74.jpeg?itok=IhG3LB9w	Haines A74
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A76.jpeg?itok=HVTXFXXs	Haines A76
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A78.jpeg?itok=21xPR4Pp	Haines A78
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Haines-A82.jpeg?itok=IOjcsJxq	Haines A82
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-135.jpeg?itok=ejKoQHYY	Kaplan 135
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-169.jpeg?itok=TAjkQJTO	Kaplan 169
https://dts.ucla.edu/sites/default/files/styles/media_library/public/media/images/KAPLAN_A26-Full_Room-2-320x213.jpg?itok=fmlogLsa	Kaplan A26
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-A30.jpeg?itok=tuNthTfe	Kaplan A30
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-A32.jpeg?itok=MNLcI3M_	Kaplan A32
https://dts.ucla.edu/sites/default/files/styles/media_library/public/media/images/kaplan-A40.png?itok=avCcm55e	Kaplan A40
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-A46.jpeg?itok=kaPc9B06	Kaplan A46
https://dts.ucla.edu/sites/default/files/styles/media_library/public/media/images/kaplan-A48.png?itok=1qWKpGUX	Kaplan A48
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-A51.jpeg?itok=X4pabUzh	Kaplan A51
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-A56.jpeg?itok=qMdqG-Vt	Kaplan A56
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-A60.jpeg?itok=xoTS9Icw	Kaplan A60
https://dts.ucla.edu/sites/default/files/styles/media_library/public/media/images/KAPLAN_A65-Full_Room-320x213.jpg?itok=zIMLJENu	Kaplan A65
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-A66.jpeg?itok=P6TUHQ2Q	Kaplan A66
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Humanities-A68.jpeg?itok=gh9e19VQ	Kaplan A68
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Kaufman-101.jpeg?itok=rvUkcDYL	Kaufman 101
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Kaufman-136.jpeg?itok=Nf02JPiS	Kaufman 136
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Kaufman-153.jpeg?itok=tyr3OmRp	Kaufman 153
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Kinsey%20Pavilion-1200B.jpeg?itok=9qmGlAcL	Kinsey Pavilion 1200B
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Kinsey%20Pavilion-1220B.jpeg?itok=A9K4YiP1	Kinsey Pavilion 1220B
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Kinsey%20Pavilion-1240B.jpeg?itok=dZ1SzOrk	Kinsey Pavilion 1240B
https://dts.ucla.edu/sites/default/files/styles/media_library/public/La%20Kretz-100.jpeg?itok=pxbgHABQ	La Kretz 100
https://dts.ucla.edu/sites/default/files/styles/media_library/public/La%20Kretz-101.jpeg?itok=G3mnEUZ9	La Kretz 101
https://dts.ucla.edu/sites/default/files/styles/media_library/public/La%20Kretz-110.jpeg?itok=PfCRZ-zc	La Kretz 110
https://dts.ucla.edu/sites/default/files/styles/media_library/public/La%20Kretz-120.jpeg?itok=j-NtEXH7	La Kretz 120
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-3915A.jpeg?itok=fia5O8ga	Mathematical Sciences 3915A
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-3915D.jpeg?itok=GyHTPPr8	Mathematical Sciences 3915D
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-3915G.jpeg?itok=XO6Ruvy9	Mathematical Sciences 3915G
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-3915H.jpeg?itok=t4Rrtao5	Mathematical Sciences 3915H
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-4000A.jpeg?itok=dKAyrzGx	Mathematical Sciences 4000A
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5117.jpeg?itok=sWDW4Vqg	Mathematical Sciences 5117
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5118.jpeg?itok=gIR2cvgY	Mathematical Sciences 5118
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5127.jpeg?itok=PRqJBNEj	Mathematical Sciences 5127
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5128.jpeg?itok=7ppX-Sgh	Mathematical Sciences 5128
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5137.jpeg?itok=eYOEBfaK	Mathematical Sciences 5137
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5138.jpeg?itok=iNa3tyBV	Mathematical Sciences 5138
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5147.jpeg?itok=6KZKofMH	Mathematical Sciences 5147
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5148.jpeg?itok=ARSWDDrQ	Mathematical Sciences 5148
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5200.jpeg?itok=2o_LTzx1	Mathematical Sciences 5200
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5203.jpeg?itok=cdOgERYS	Mathematical Sciences 5203
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5217.jpeg?itok=paduju88	Mathematical Sciences 5217
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5225.jpeg?itok=Jw-_YSGZ	Mathematical Sciences 5225
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-5233.jpeg?itok=EG-sghTo	Mathematical Sciences 5233
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-6201.jpeg?itok=HoZvFGE6	Mathematical Sciences 6201
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-6229.jpeg?itok=sAJ60r9n	Mathematical Sciences 6229
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Mathematical%20Sciences-7608.jpeg?itok=R6p8f1Fj	Mathematical Sciences 7608
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Moore-100.jpeg?itok=RzFgkKyb	Moore 100
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Moore-1003.jpeg?itok=FAuNsFJ5	Moore 1003
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Perloff-1102.jpeg?itok=xorYGWBX	Perloff 1102
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Physics%20%26%20Astronomy-1425.jpeg?itok=6R7O8yIR	Physics & Astronomy 1425
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Physics%20%26%20Astronomy-1434A.jpeg?itok=By-JYbDF	Physics & Astronomy 1434A
https://dts.ucla.edu/sites/default/files/styles/media_library/public/media/images/pab-1749.png?itok=DV9oingl	Physics & Astronomy 1749
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Physics%20%26%20Astronomy-2434.jpeg?itok=4wbh1kOm	Physics & Astronomy 2434
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Physics%20%26%20Astronomy-2748.jpeg?itok=4iGBrH8O	Physics & Astronomy 2748
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1222.jpeg?itok=nGzxc9p3	Public Affairs 1222
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1234.jpeg?itok=6NCsaCpc	Public Affairs 1234
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1246.jpeg?itok=LUOfmziQ	Public Affairs 1246
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1256.jpeg?itok=Ve2AGOq1	Public Affairs 1256
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1264.jpeg?itok=RrZaGsFJ	Public Affairs 1264
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1270.jpeg?itok=pnXrvSwn	Public Affairs 1270
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1278.jpeg?itok=10C6CJWU	Public Affairs 1278
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1284.jpeg?itok=VMVinaIM	Public Affairs 1284
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1323.jpeg?itok=_dEFAPmR	Public Affairs 1323
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1329.jpeg?itok=5jbDKOlW	Public Affairs 1329
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1337.jpeg?itok=BO14naGk	Public Affairs 1337
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-1343.jpeg?itok=4TVYwecs	Public Affairs 1343
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2214.jpeg?itok=2LkhlaID	Public Affairs 2214
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2232.jpeg?itok=IYSVnBsp	Public Affairs 2232
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2238.jpeg?itok=xjlC0ukx	Public Affairs 2238
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2242.jpeg?itok=uP6kaP_M	Public Affairs 2242
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2250.jpeg?itok=cKaN1eF2	Public Affairs 2250
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2270.jpeg?itok=V5Ulcepm	Public Affairs 2270
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2278.jpeg?itok=vxqSwVMJ	Public Affairs 2278
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2284.jpeg?itok=lqq5AUyz	Public Affairs 2284
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2292.jpeg?itok=1BToGwbI	Public Affairs 2292
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2317.jpeg?itok=5_U7C72V	Public Affairs 2317
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2319.jpeg?itok=S-10aYMs	Public Affairs 2319
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2325.jpeg?itok=iF7OoH6G	Public Affairs 2325
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Public%20Affairs-2333.jpeg?itok=mJW6ZNIj	Public Affairs 2333
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-1200.jpeg?itok=qrh-9pem	Rolfe 1200
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3105.jpeg?itok=pfpqFAQp	Rolfe 3105
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3108.jpeg?itok=iJ9RLEMz	Rolfe 3108
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3115.jpeg?itok=FoRlWWyu	Rolfe 3115
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3116.jpeg?itok=GsxgWppF	Rolfe 3116
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3120.jpeg?itok=2HoT0tmG	Rolfe 3120
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3121.jpeg?itok=DpY9mtqQ	Rolfe 3121
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3126.jpeg?itok=RSUNF3mS	Rolfe 3126
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3129.jpeg?itok=2b9lBenK	Rolfe 3129
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3134.jpeg?itok=Fb6FBoty	Rolfe 3134
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Rolfe-3135.jpeg?itok=Go0iWtxE	Rolfe 3135
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-148.jpeg?itok=MUHfg-_8	Royce 148
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-150.jpeg?itok=EhC-LrG5	Royce 150
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-152.jpeg?itok=px0CcUIV	Royce 152
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-154.jpeg?itok=pLrf3BDY	Royce 154
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-156.jpeg?itok=8Ay_0dat	Royce 156
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-160.jpeg?itok=TAM_93wz	Royce 160
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-162.jpeg?itok=-IQFVxLV	Royce 162
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-164.jpeg?itok=Jum6LL4h	Royce 164
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-166.jpeg?itok=8O3GWQvm	Royce 166
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-190.jpeg?itok=XsIKQUMF	Royce 190
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Royce-362.jpeg?itok=zf--_Vsh	Royce 362
https://dts.ucla.edu/sites/default/files/styles/media_library/public/Slichter-2834.jpeg?itok=AsbTNFi3	Slichter 2834
https://dts.ucla.edu/sites/default/files/styles/media_library/public/WG%20Young-1044.jpeg?itok=N0_fpHhs	WG Young 1044
https://dts.ucla.edu/sites/default/files/styles/media_library/public/WG%20Young-2200.jpeg?itok=_T_DODFq	WG Young 2200
https://dts.ucla.edu/sites/default/files/styles/media_library/public/WG%20Young-4216.jpeg?itok=L4cIyKh8	WG Young 4216
https://dts.ucla.edu/sites/default/files/styles/media_library/public/WG%20Young-CS%2024.jpeg?itok=BJC8RGBW	WG Young CS 24
https://dts.ucla.edu/sites/default/files/styles/media_library/public/WG%20Young-CS%2050.jpeg?itok=T4kblogx	WG Young CS 50
https://dts.ucla.edu/sites/default/files/styles/media_library/public/WG%20Young-CS%2076.jpeg?itok=mIwP9E-s	WG Young CS 76
"""

# Parse image data
image_map = {}
for line in image_data.strip().split('\n'):
    if '\t' in line:
        url, room_name = line.split('\t')
        # Normalize the room name for matching
        room_name_normalized = room_name.upper().strip()
        image_map[room_name_normalized] = url

print(f"Loaded {len(image_map)} image URLs")

# Load classrooms.json
with open('classrooms.json', 'r') as f:
    classrooms = json.load(f)

# Match and add image URLs
matches = 0
for room in classrooms:
    if not room.get('offered'):
        continue
    
    # Try to match the room
    room_text = room['text'].upper().strip()
    
    # Direct match
    if room_text in image_map:
        room['image_url'] = image_map[room_text]
        matches += 1
        continue
    
    # Try matching with building and room number
    building = room['building'].upper().strip()
    room_num = room['room'].lstrip('0').upper()  # Remove leading zeros
    
    # Handle special building name mappings
    building_map = {
        'HUMANTS': 'KAPLAN',
        'PAB': 'PHYSICS & ASTRONOMY',
        'PUB AFF': 'PUBLIC AFFAIRS',
        'MS': 'MATHEMATICAL SCIENCES',
        'KNSY_PAV': 'KINSEY PAVILION',
        'LA KRETZ': 'LA KRETZ',
        'WG YOUNG': 'WG YOUNG',
    }
    
    mapped_building = building_map.get(building, building)
    
    # Try various combinations
    possible_matches = [
        f"{mapped_building} {room_num}",
        f"{building} {room_num}",
        # Handle buildings with spacing issues
        room_text.replace('  ', ' '),
    ]
    
    for variant in possible_matches:
        if variant in image_map:
            room['image_url'] = image_map[variant]
            matches += 1
            print(f"Matched: {room_text} -> {variant}")
            break

print(f"\nMatched {matches} rooms with images")

# Save updated JSON
with open('classrooms.json', 'w') as f:
    json.dump(classrooms, f, indent=1)

print("Updated classrooms.json with image URLs")
