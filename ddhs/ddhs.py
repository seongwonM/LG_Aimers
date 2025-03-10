import torch
from torch import nn
from torch.nn import functional as F
from torch.utils.data import DataLoader, Dataset
from sklearn.neighbors import KernelDensity
from sklearn.preprocessing import StandardScaler
import numpy as np
import pandas as pd



# autoencoder
class Autoencoder(nn.Module):
  def __init__(self, input_dim, latent_dim):
      super().__init__()
      self.encoder = nn.Sequential(
           nn.Linear(input_dim, 256),
          nn.ReLU(),
          nn.Linear(256, 128),
          nn.ReLU(),
          nn.Linear(128, latent_dim)
        )
      self.decoder = nn.Sequential(
          nn.Linear(latent_dim, 128),
          nn.ReLU(),
          nn.Linear(128, 256),
          nn.ReLU(),
          nn.Linear(256, input_dim)
        )
      self.centers = nn.Parameter(torch.randn(2, latent_dim))

  def forward(self, x):
      encoded = self.encoder(x)
      decoded = self.decoder(encoded)
      return encoded, decoded

  def get_center_loss(self, encoded, target):
      batch_size = encoded.size(0)
      target = target.reshape(-1, 1) #   view
      centers_batch = self.centers.gather(0, target.to(torch.int64).repeat(1, encoded.size(1)))
      center_loss = (encoded - centers_batch).pow(2).sum() / batch_size
      return center_loss



# imbalanced에 data level로 해결하는 모델
class DDHS:
  #데이터를 KDE로 가우시안 분포를 이용하여 중간 %를 추출하는 함수
  # start, last에 퍼센트를 입력

  def extract_middle_percent(self,data, start, last):
    scaler = StandardScaler()
    data_scaled = scaler.fit_transform(data)

    # kde가 큰 데이터셋에서 오래 걸려서서 percentile로 간소화
    if len(data_scaled) > 10000 :
      threshold_low, threshold_high = np.percentile(data_scaled, [start, last])
      mask = np.logical_and(data_scaled >= threshold_low, data_scaled <= threshold_high)
      mask = [True if i.sum() > len(i)/2 else False for i in mask]
    else:
      kde = KernelDensity(kernel='gaussian', bandwidth=0.5).fit(data_scaled)
      log_prob = kde.score_samples(data_scaled)
      prob = np.exp(log_prob)
      threshold_low, threshold_high = np.percentile(prob, [start, last])
      mask = np.logical_and(prob >= threshold_low, prob <= threshold_high) #######

    data_middle = data[mask]

    if len(data_middle) > 0 :
      return data_middle
    else:
      print("No middle 50% found, returning original data")
      return np.array([])

  #  각 feature 안의 값을 복원추출하는 함수

  def reconstruct_features(self,data):
    mean = data.mean(axis=0)
    std = data.std(axis=0)
    reconstructed = np.random.randn(*data.shape) * std + mean
    return reconstructed

  # synthetic sample을 생성하는 함수
  # 라벨과과 데이터를 하나의 데이터프레임으로 출력
  # small class / large class의 비율 = ratio


  def generate_synthetic_sample(self,X,Y, ratio=1):

    data = pd.concat([X,Y],axis=1)

    # small class
    data_A = data[data[Y.columns[0]]== Y.value_counts().idxmin()[0]  ].loc[:, data.columns != Y.columns[0]].astype(float).values

    # large class
    data_B = data[data[Y.columns[0]]== Y.value_counts().idxmax()[0] ].loc[:, data.columns != Y.columns[0]].astype(float).values

    # autoencoder를 사용하여 잠재 변수를 추출
    with torch.no_grad():
        encoded_A, _ = self.model(torch.tensor(data_A).float())
        encoded_B, _ = self.model(torch.tensor(data_B).float())

    # Majority : 입력받은 퍼센트 보다 Density가 큰 샘플을 Keep→ 입력받은 퍼센트 샘플을 Classifier 의 Train 데이터로 활용
    # Minority : 입력받은 퍼센트 을 사용해서 더 높은 기준을 설정 → 입력받은 퍼센트 샘플을 Classifier 의 Train 데이터로 활용 → 25% 샘플을 Subsequence 생성 과정의 샘플로 활용

    encoded_A_middle = self.extract_middle_percent(encoded_A.cpu().numpy(),50 - self.small_percent/2 , 50+ self.small_percent/2)
    encoded_B_middle = self.extract_middle_percent(encoded_B.cpu().numpy(),50 -self.large_percent/2, 50 + self.large_percent/2)

    # 중간 25%의 잠재 변수로부터 feature를 복원추출
    reconstructed_features = self.reconstruct_features(self.extract_middle_percent(encoded_A.cpu().numpy(),37.5, 62.5))
    # 임의의 위치에 synthetic sample 생성
    center_A = np.mean(encoded_A.cpu().numpy(), axis=0, dtype=np.float64, out=None)

    center_B = np.mean(encoded_B.numpy(), axis=0, dtype=np.float64, out=None)

    radius_A = np.max(np.linalg.norm(encoded_A.cpu().numpy() - center_A, axis=1))

    synthetic_sample = pd.DataFrame(reconstructed_features) # 최종 합치기

   # 합성된 개수 / 원래 클래스 개수
    while len(synthetic_sample)/len(data_A) >= ratio :
        z = np.random.randn(latent_dim)
        print(z)
        if np.linalg.norm(z - center_A) < np.linalg.norm(z - center_B) and np.linalg.norm(z - center_A) < radius_A:
            synthetic_sample.append(z) #, ignore_index=True)
            print('들어가는',z)
    # 최종 출력할 데이터
    encoded_B_middle = pd.DataFrame(encoded_B_middle)
    encoded_B_middle['label'] = Y.value_counts().idxmax()[0]

    encoded_A_middle = pd.DataFrame(encoded_A_middle)
    encoded_A_middle['label'] = Y.value_counts().idxmin()[0]

    synthetic_sample['label'] = Y.value_counts().idxmin()[0]

    ouput = pd.concat([encoded_B_middle,encoded_A_middle,synthetic_sample ] )

    x_ = ouput.loc[:, ouput.columns != 'label']
    x_.columns = X.columns
    y_ = ouput['label']
    y_.columns = Y.columns

    return x_ , y_

  def fit(self,X,Y,large_percent = 50 , small_percent = 75 ,lr = 1e-3 ,num_epochs = 50, ratio = 1):
    self.ratio = ratio
    self.large_percent = large_percent
    self.small_percent = small_percent
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    input_dim = len(X.columns) # 데이터의 차원
    latent_dim = len(X.columns) # 잠재 변수의 차원
    #ratio =  합성된 데이터 수/small class  비율
    #large_percent : large class의 추출 비율
    #small_percent : small class의 추출 비율
    #lr = 1e-3 # 학습률
    #num_epochs = 50 # 학습 에폭 수

    self.model = Autoencoder(input_dim, latent_dim).to(device)
    optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)


    for epoch in range(num_epochs):
      x = torch.tensor(X.to_numpy()).float()#.to(device)
      y = torch.tensor(Y[Y.columns[0]].to_numpy()).float().to(device)

      encoded, decoded = self.model(x)
      reconstruction_loss = F.mse_loss(decoded, x)
      center_loss = self.model.get_center_loss(encoded, y)

      loss = reconstruction_loss + center_loss
      cross_entropy_loss = F.cross_entropy(decoded, y.long()) # y를 long 형으로 요구
      loss += cross_entropy_loss

      optimizer.zero_grad()
      loss.backward()
      optimizer.step()


    synthetic_X, synthetic_Y = self.generate_synthetic_sample(X,Y, self.ratio)
    return synthetic_X, synthetic_Y


  def __init__(self):
    self.result = 0