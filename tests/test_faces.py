"""人脸检测的框运算与裁剪几何。

这里全是坐标算术，而坐标算错**不会报错**：偏了的框裁出来仍然是一张「像脸」的图，
送进 CCIP 仍然得到一个向量，仍然能算距离、仍然能归队。
症状要到「角色过滤偶尔选错镜头」才显形，那时已经查不动了。

不测模型本身（那要下 1.9 亿参数的权重），只测夹在模型两头的这些纯函数。
"""

import numpy as np
import pytest

from pipeline import faces


class TestXywh2xyxy:
    def test_中心宽高转左上右下(self):
        out = faces._xywh2xyxy(np.array([[10.0, 20.0, 4.0, 6.0]]))
        assert out.tolist() == [[8.0, 17.0, 12.0, 23.0]]

    def test_不改原数组(self):
        # 原地改会污染调用方后面还要用的 scores 切片
        x = np.array([[10.0, 20.0, 4.0, 6.0]])
        faces._xywh2xyxy(x)
        assert x.tolist() == [[10.0, 20.0, 4.0, 6.0]]

    def test_批量(self):
        out = faces._xywh2xyxy(np.array([[10.0, 10.0, 2.0, 2.0], [50.0, 50.0, 10.0, 10.0]]))
        assert out.tolist() == [[9.0, 9.0, 11.0, 11.0], [45.0, 45.0, 55.0, 55.0]]


class TestNms:
    def test_重叠的框只留分高的(self):
        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [1.0, 1.0, 11.0, 11.0]])
        keep = faces._nms(boxes, np.array([0.9, 0.8]), 0.5)
        assert keep == [0]

    def test_不重叠的框都留(self):
        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [100.0, 100.0, 110.0, 110.0]])
        keep = faces._nms(boxes, np.array([0.5, 0.9]), 0.5)
        assert sorted(keep) == [0, 1]

    def test_按分数降序返回(self):
        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [100.0, 100.0, 110.0, 110.0]])
        assert faces._nms(boxes, np.array([0.5, 0.9]), 0.5) == [1, 0]

    def test_同一场戏里两个人不该被并掉(self):
        # 正反打里两张脸常常挨得很近。阈值调过头会把其中一个人整段抹掉，
        # 而抹掉之后「这个镜头里没有他」看起来完全正常。
        boxes = np.array([[0.0, 0.0, 20.0, 20.0], [18.0, 0.0, 38.0, 20.0]])
        keep = faces._nms(boxes, np.array([0.9, 0.85]), faces.FACE_IOU)
        assert sorted(keep) == [0, 1]

    def test_单个框(self):
        assert faces._nms(np.array([[0.0, 0.0, 5.0, 5.0]]), np.array([0.9]), 0.5) == [0]


class TestCrop:
    def img(self, w=400, h=300):
        from PIL import Image
        return Image.new("RGB", (w, h), (128, 128, 128))

    def test_裁的是正方形(self):
        # CCIP 会把图直缩到 384×384，喂非正方形进去等于把脸拉变形
        out = faces.crop(self.img(), (100, 100, 140, 160), expand=1.0)
        assert out.width == out.height

    def test_边长按长边算(self):
        # 框是 40×60，长边 60，expand=1.0 → 60×60
        out = faces.crop(self.img(), (100, 100, 140, 160), expand=1.0)
        assert (out.width, out.height) == (60, 60)

    def test_扩边把头发包进来(self):
        # 动漫角色的身份信息大半在头发上，而检测框只框脸
        out = faces.crop(self.img(), (100, 100, 140, 160), expand=2.0)
        assert (out.width, out.height) == (120, 120)

    def test_中心不变(self):
        # 扩边必须以框中心为心。偏心裁会系统性地切掉一侧的头发，
        # 而切掉之后仍然是一张脸，仍然算得出向量。
        #
        # 判据是**在原图框中心打一个白点，看它落在裁片的正中**——
        # 只比裁片尺寸是证伪不了偏心的（偏了尺寸照样对）。
        im = self.img()
        box = (100, 100, 140, 160)
        im.putpixel(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), (255, 255, 255))
        for expand in (1.0, 2.0, 1.6):
            out = faces.crop(im, box, expand)
            assert out.getpixel((out.width // 2, out.height // 2)) == (255, 255, 255), expand

    def test_越界时补黑而不是挪位置(self):
        # 贴边的脸扩边会出界。PIL 用黑色补，尺寸仍然对——
        # 若改成夹住边界，裁出来的框会偏心，脸就不在中间了。
        out = faces.crop(self.img(), (0, 0, 40, 40), expand=2.0)
        assert (out.width, out.height) == (80, 80)


class TestConstants:
    def test_领先量小于同人上界(self):
        # 反了的话没有任何一张脸能同时满足两条判据，索引会安安静静地全空
        assert faces.CCIP_MARGIN < faces.CCIP_SAME

    def test_同人上界比作者的跨番值严(self):
        # 作者的 0.1785 是跨多部番标的。同一部番里角色是同一个人设计的，
        # 距离整体压缩，照搬会把八幡、海老名、路人一起判成阳乃——实测过。
        assert faces.CCIP_SAME < faces.CCIP_AUTHOR_THRESHOLD

    def test_归一化用的是_CLIP_的均值方差(self):
        # 抄成 ImageNet 的不会报错，只会让所有距离一起漂，
        # 而阈值是按作者那套标定的，漂了就全不准
        assert faces.CCIP_MEAN[0] == pytest.approx(0.48145466)
        assert faces.CCIP_STD[0] == pytest.approx(0.26862954)
