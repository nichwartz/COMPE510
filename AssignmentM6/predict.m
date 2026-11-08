function p = predict(beta1, beta2, X)
%PREDICT Predict the label of an input given a trained neural network
%   p = PREDICT(beta1, beta2, X) outputs the probability of the output to 
%   1, given input X and trained weights of a neural network (beta1, beta2)

% You need to return the following variables correctly 
p = zeros(size(X, 1), 1); % 

% ====================== YOUR CODE HERE ======================
% Instructions: Complete the following code to make predictions using
%               your learned neural network. 
%
%
X = [ones(size(X,1),1) X];

hiddenLayer = sigmoid(X * beta1');
hiddenLayer = [ones(size(hiddenLayer, 1), 1) hiddenLayer];
p = sigmoid(hiddenLayer * beta2');
p = p >= 0.5;





% =========================================================================


end
